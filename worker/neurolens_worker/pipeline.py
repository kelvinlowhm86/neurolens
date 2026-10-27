"""
One job, end to end:

  claim → download → ffprobe + resize credit hold → full pass → silent pass → ROI → write result → capture

Each stage stamps its Experiment 1 timestamp in `jobs`. A heartbeat thread keeps the DB row and the
SQS message alive while the GPU works, and reacts to a Spot interruption straight away (even in the
middle of a pass) by handing the job back to the queue.
"""

import json
import logging
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from neurolens_common import db, jobs, ledger, pricing, s3keys
from neurolens_common.config import Settings
from neurolens_common.jobs import JobStatus
from neurolens_common.ledger import InsufficientCredits
from neurolens_common.schemas import ResultV2

from . import media, roi
from .report import generate_report
from .errors import LostOwnership, NotEnoughCredits, PermanentJobError, SpotInterrupted, TooLong
from .inference.base import Backend
from .jobqueue import JobQueue, Message
from .lifecycle import Lifecycle
from .metrics import Metrics

logger = logging.getLogger("neurolens.worker.pipeline")


@dataclass
class _Ctx:
    job_id: UUID
    msg: Message
    lost: bool = False
    requeued: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


class JobRunner:
    def __init__(self, cfg: Settings, backend: Backend, s3, queue: JobQueue, lifecycle: Lifecycle,
                 metrics: Metrics, interrupted: threading.Event):
        self.cfg = cfg
        self.backend = backend
        self.s3 = s3
        self.queue = queue
        self.lc = lifecycle
        self.metrics = metrics
        self.interrupted = interrupted
        self.jobs_done = 0

    @property
    def worker_id(self) -> str:
        return self.lc.worker_id

    # ─── entry point ───────────────────────────────────────────────────────

    def handle(self, msg: Message) -> None:
        try:
            events = s3keys.parse_s3_event(msg.body)
        except (ValueError, KeyError, json.JSONDecodeError):
            logger.warning("Dropping unparseable message %s", msg.id)
            self.queue.delete(msg)
            return
        if not events:                       # s3:TestEvent or a key we don't own
            self.queue.delete(msg)
            return
        if len(events) > 1:
            logger.warning("Message %s has %d records; processing the first", msg.id, len(events))
        self._run(msg, events[0])

    # ─── one job ───────────────────────────────────────────────────────────

    def _run(self, msg: Message, evt: s3keys.UploadEvent) -> None:
        with db.transaction() as conn:
            job = jobs.claim_job(conn, evt.job_id, self.worker_id, evt.event_time)
        if job is None:
            logger.info("Job %s not claimable (done, cancelled or owned elsewhere); dropping message", evt.job_id)
            self.queue.delete(msg)
            return
        if job["user_id"] != evt.user_id:
            with db.transaction() as conn:
                jobs.close_job(conn, evt.job_id, JobStatus.FAILED, "INVALID_KEY", "Upload key does not match job owner")
            self.queue.delete(msg)
            return

        ctx = _Ctx(job_id=evt.job_id, msg=msg)
        wait_s = (datetime.now(timezone.utc) - evt.event_time).total_seconds()
        logger.info("Claimed job %s (attempt %d, waited %.1fs)", evt.job_id, job["attempts"], wait_s)
        hb = _Heartbeat(self, ctx)
        hb.start()
        scratch = self.cfg.scratch_dir / str(evt.job_id)
        scratch.mkdir(parents=True, exist_ok=True)
        try:
            self._process(ctx, evt, job, scratch, wait_s)
        except PermanentJobError as e:
            logger.info("Job %s rejected: %s (%s)", evt.job_id, e, e.code)
            with db.transaction() as conn:
                jobs.close_job(conn, evt.job_id, JobStatus.FAILED, e.code, str(e))
            self.queue.delete(msg)
            self.metrics.count("JobsFailed")
        except SpotInterrupted:
            self.requeue(ctx)
        except LostOwnership:
            logger.warning("Lost ownership of job %s; another worker has it", evt.job_id)
        except Exception as e:
            if ctx.requeued:                 # instance is going away; the job is already back in the queue
                logger.warning("Job %s errored after Spot requeue: %s", evt.job_id, e)
                return
            logger.exception("Job %s failed; SQS will retry", evt.job_id)
            with db.transaction() as conn:
                jobs.release_for_retry(conn, evt.job_id, self.worker_id, f"{type(e).__name__}: {e}")
            self.queue.retry_soon(msg)
        finally:
            hb.stop()
            shutil.rmtree(scratch, ignore_errors=True)
            self.backend.end_job()

    def _process(self, ctx: _Ctx, evt: s3keys.UploadEvent, job: dict, scratch, wait_s: float) -> None:
        local = scratch / f"input{evt.ext}"
        self.s3.download_file(evt.bucket, evt.key, str(local))
        self._stage(ctx, "VALIDATING", 10, finished="DOWNLOADING")

        probe = media.probe(local)
        if probe.duration_s > self.cfg.max_video_seconds:
            raise TooLong(f"Video is {probe.duration_s:.0f} s; the limit is {self.cfg.max_video_seconds} s")
        credits = pricing.credits_for_duration(probe.duration_s, self.cfg)
        try:
            with db.transaction() as conn:
                jobs.set_actual_duration(conn, ctx.job_id, self.worker_id, round(probe.duration_s, 2))
                ledger.adjust(conn, ctx.job_id, credits)
        except InsufficientCredits as e:
            raise NotEnoughCredits(f"This video needs {credits} credits: {e}") from e
        self._stage(ctx, "PASS_FULL", 20, finished="VALIDATING")

        t0 = time.monotonic()
        full = self.backend.predict_full(local, probe.duration_s)
        t_full = time.monotonic() - t0
        transcript = self.backend.transcript()
        self._stage(ctx, "PASS_SILENT", 55, finished="PASS_FULL")

        t0 = time.monotonic()
        silent = self.backend.predict_silent(local, probe.duration_s)
        t_silent = time.monotonic() - t0
        self._stage(ctx, "ROI", 85, finished="PASS_SILENT")

        t0 = time.monotonic()
        extracted = roi.extract(full, silent, self.backend.roi_masks)
        t_roi = time.monotonic() - t0
        self._stage(ctx, "WRITING", 95, finished="ROI")

        gpu, vram = self.backend.gpu_device(), self.backend.peak_vram_gb()
        result = ResultV2(
            job_id=str(ctx.job_id),
            filename=job["original_filename"],
            model={"name": self.cfg.tribe_repo, "revision": self.backend.model_revision},
            processing={"pass_full_s": round(t_full, 2), "pass_silent_s": round(t_silent, 2), "gpu": gpu,
                        "peak_vram_gb": vram, "backend": self.backend.name, "silent_mode": self.cfg.silent_mode},
            **extracted,
        )
        key = s3keys.result_key(evt.user_id, ctx.job_id)
        self.s3.put_object(Bucket=evt.bucket, Key=key, Body=result.model_dump_json().encode(),
                           ContentType="application/json")

        report_on = self.cfg.report_llm != "off"
        with db.transaction() as conn:
            done = jobs.complete_job(conn, ctx.job_id, self.worker_id, credits=credits, result_key=key,
                                     summary=extracted["summary"], gpu_device=gpu, peak_vram_gb=vram,
                                     cold_start=self.jobs_done == 0)
            if done:
                jobs.set_report_status(conn, ctx.job_id, "PENDING" if report_on else "OFF")
        if not done:
            raise LostOwnership()
        self.queue.delete(ctx.msg)
        self.jobs_done += 1
        self.lc.job_done()
        self.metrics.put({"QueueWaitSeconds": wait_s, "PassFullSeconds": t_full,
                          "PassSilentSeconds": t_silent, "RoiSeconds": t_roi})
        self.metrics.put({"PeakVramGB": vram}, unit="None")
        self.metrics.count("JobsSucceeded")
        logger.info("Job %s done: %.0fs video, full %.1fs, silent %.1fs, %d credits",
                    ctx.job_id, probe.duration_s, t_full, t_silent, credits)
        if report_on:
            self._report(ctx.job_id, evt, result.model_dump(), local, transcript, scratch)

    def _report(self, job_id, evt: s3keys.UploadEvent, result: dict, video, transcript: list[dict], scratch) -> None:
        """Step after TRIBE: LLM summary → PDF. The job has already succeeded; a failure only affects the report."""
        t0 = time.monotonic()
        try:
            pdf, model = generate_report(self.cfg, result, video, transcript, scratch / "report")
            key = s3keys.report_key(evt.user_id, job_id)
            self.s3.put_object(Bucket=evt.bucket, Key=key, Body=pdf.read_bytes(), ContentType="application/pdf")
            with db.transaction() as conn:
                jobs.set_report_status(conn, job_id, "READY", key=key, model=model)
            self.metrics.put({"ReportSeconds": time.monotonic() - t0})
            logger.info("Report for job %s ready (%s, %.1fs)", job_id, model, time.monotonic() - t0)
        except Exception as e:
            logger.exception("Report for job %s failed", job_id)
            with db.transaction() as conn:
                jobs.set_report_status(conn, job_id, "FAILED", error=f"{type(e).__name__}: {e}")
            self.metrics.count("ReportsFailed")

    # ─── helpers ───────────────────────────────────────────────────────────

    def _stage(self, ctx: _Ctx, stage: str, pct: int, finished: str) -> None:
        """Advance progress; stop if interrupted or if another worker has taken the job."""
        if self.interrupted.is_set():
            raise SpotInterrupted()
        if ctx.lost:
            raise LostOwnership()
        with db.transaction() as conn:
            if not jobs.set_stage(conn, ctx.job_id, self.worker_id, stage, pct, finished_stage=finished):
                raise LostOwnership()

    def requeue(self, ctx: _Ctx) -> None:
        """Hand the job back after a Spot notice. Safe to call from the heartbeat thread and the job thread."""
        with ctx.lock:
            if ctx.requeued:
                return
            with db.transaction() as conn:
                mine = jobs.requeue_job(conn, ctx.job_id, self.worker_id)
            if mine:
                self.queue.requeue_fresh(ctx.msg, "spot")
                self.metrics.count("SpotInterruptions")
                logger.warning("Job %s handed back to the queue (Spot interruption)", ctx.job_id)
            ctx.requeued = True
            ctx.lost = True


class _Heartbeat(threading.Thread):
    """Keeps the job row fresh and the SQS message hidden; reacts to Spot notices within ~2 s."""

    def __init__(self, runner: JobRunner, ctx: _Ctx, tick_s: float = 2.0):
        super().__init__(name=f"heartbeat-{ctx.job_id}", daemon=True)
        self.runner = runner
        self.ctx = ctx
        self.tick_s = tick_s
        self._stop_event = threading.Event()

    def run(self) -> None:
        cfg = self.runner.cfg
        last_beat = time.monotonic()
        while not self._stop_event.wait(self.tick_s):
            if self.runner.interrupted.is_set():
                self.runner.requeue(self.ctx)
                return
            if time.monotonic() - last_beat < cfg.heartbeat_seconds:
                continue
            last_beat = time.monotonic()
            try:
                with db.transaction() as conn:
                    alive = jobs.heartbeat(conn, self.ctx.job_id, self.runner.worker_id)
                if not alive:
                    self.ctx.lost = True
                    return
                self.runner.queue.extend(self.ctx.msg, cfg.visibility_extend_seconds)
            except Exception:
                logger.warning("Heartbeat failed for job %s", self.ctx.job_id, exc_info=True)

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=5)
