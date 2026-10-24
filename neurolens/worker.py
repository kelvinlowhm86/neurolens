"""The SageMaker GPU container (docs/M3e_spec.md §4).

`python worker.py serve` loads the model, warms it up, writes a boot record and listens on port
8080. SageMaker POSTs one video per request to /invocations; the job logic claims the job, runs
the analysis, writes the result and charges or refunds it. `/ping` answers while a job runs.
"""

import contextlib
import enum
import json
import logging
import os
import shutil
import socket
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from neurolens import billing, inference, media, settings, storage
from neurolens import db as dbmod
from neurolens.response import extract_response

logger = logging.getLogger("neurolens")

REQUIRED_CONFIG = [
    (("aws", "region"), "aws.region (NEUROLENS_AWS_REGION)"),
    (("aws", "s3_bucket"), "aws.s3_bucket (NEUROLENS_S3_BUCKET)"),
    (("worker", "heartbeat_seconds"), "worker.heartbeat_seconds"),
    (("worker", "max_job_minutes"), "worker.max_job_minutes"),
]
MAX_ERROR_CHARS = 200  # the job's error_message is shown to the user as is
MAX_HEARTBEAT_SECONDS = 50
VIDEO_CHANGED_ALLOWANCE_MS = 1000  # slack between the dispatcher's and the local duration reading
JOB_HEADER = "X-Amzn-SageMaker-Custom-Attributes"
BODY_READ_TIMEOUT_S = 60  # a request body that stalls this long gives up and frees the slot
PORT = 8080


class Outcome(enum.Enum):
    """What happened to one job. Every outcome is final for the request."""

    DONE = "done"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"  # refunded before inference (unreadable, never dispatched)
    SKIPPED = "skipped"  # unknown, already finished, or held by another worker
    FAILED = "failed"
    LOST_CLAIM = "lost_claim"


class _ClaimLost(Exception):
    """A billing call reported that this worker no longer holds the job."""


class ClaimKeepAlive:
    """Calls `on_beat` every interval, and stops after max_seconds (logged as an error).

    SageMaker's 60-minute invocation timeout is longer than the configured job limit, so a hung
    job's claim goes stale before the error handler refunds it. `on_overrun` runs once at the
    limit, after the error log: the container uses it to end the process so SageMaker replaces
    the stuck instance.
    """

    def __init__(self, on_beat, interval_seconds, max_seconds, on_overrun=None):
        self._on_beat = on_beat
        self._interval = interval_seconds
        self._max_seconds = max_seconds
        self._on_overrun = on_overrun
        self._stopped = threading.Event()
        self._thread = None
        self._entered = None

    def __enter__(self):
        self._entered = time.monotonic()
        self._thread = threading.Thread(target=self._beat, name="claim-keep-alive", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stopped.set()
        return False

    def _beat(self):
        while not self._stopped.wait(self._interval):
            if time.monotonic() - self._entered >= self._max_seconds:
                logger.error(
                    f"Job still running after {self._max_seconds:.0f}s: no longer extending its "
                    "claim, so the error handler can refund it"
                )
                if self._on_overrun is not None:
                    self._on_overrun()
                return
            try:
                if self._on_beat() is False:
                    logger.warning("The claim was lost; stopping the keep-alive")
                    return
            except Exception:
                logger.warning("Keep-alive beat failed; trying again next interval", exc_info=True)


def validate_config(cfg):
    """Raise a clear error naming the first missing key the container needs."""
    for path, name in REQUIRED_CONFIG:
        node = cfg
        for part in path:
            if not isinstance(node, dict) or part not in node:
                raise ValueError(
                    f"Missing required setting: {name}. Deployment values come from the "
                    "environment; behaviour settings from config.json."
                )
            node = node[part]
    if cfg["worker"]["heartbeat_seconds"] > MAX_HEARTBEAT_SECONDS:
        raise ValueError(
            f"worker.heartbeat_seconds must be at most {MAX_HEARTBEAT_SECONDS} (a claim goes "
            "stale after 90 s without a beat)."
        )


def _log_temp_state(base, feature_cache):
    """The disk's free space and anything left in the base temp folder besides the feature cache."""
    try:
        free = shutil.disk_usage(base).free
        cache = None if feature_cache is None else str(feature_cache)
        left = [entry for entry in Path(base).iterdir() if str(entry) != cache]
        logger.info(
            f"Temporary folder cleaned: {free} bytes free on {base}; "
            f"{len(left)} entries left besides the feature cache"
        )
    except OSError:
        logger.exception(f"Could not inspect the temporary folder {base}")


@contextlib.contextmanager
def job_temp_dir(feature_cache=None):
    """A fresh folder for one job, under the base temp folder and never inside the feature cache.

    `tempfile.tempdir` and TMPDIR point at it for the job, so the audio and transcript the model
    extracts land there too. Both are restored and the folder is removed whole whatever happens.
    One job runs at a time (M3e §4a), so the process-wide temporary folder is safe to move.
    """
    base = tempfile.gettempdir()
    folder = tempfile.mkdtemp(prefix="job-", dir=base)
    previous_tempdir = tempfile.tempdir
    previous_tmpdir = os.environ.get("TMPDIR")
    tempfile.tempdir = folder
    os.environ["TMPDIR"] = folder
    try:
        yield Path(folder)
    finally:
        tempfile.tempdir = previous_tempdir
        if previous_tmpdir is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = previous_tmpdir
        try:
            shutil.rmtree(folder)
        except OSError:
            logger.exception(f"Could not remove the job's temporary folder {folder}")
        _log_temp_state(base, feature_cache)


def _error_text(err):
    """A short user-facing error: the exception's first line, never a traceback."""
    lines = [line.strip() for line in str(err).splitlines() if line.strip()]
    text = lines[0] if lines else type(err).__name__
    if "Traceback" in text:
        text = type(err).__name__
    if len(text) > MAX_ERROR_CHARS:
        text = text[: MAX_ERROR_CHARS - 1] + "…"
    return text


def _reject(s3, db, bucket, key, job_id, attempt, reason, message=None):
    """Refund a job that can never succeed, then delete its upload."""
    if not billing.issue_refund(db, job_id, reason, message, attempt=attempt):
        raise _ClaimLost
    logger.warning(f"Rejected and refunded s3://{bucket}/{key}: {reason}")
    try:
        s3.delete_object(Bucket=bucket, Key=key)
    except Exception:
        logger.exception(f"Could not delete the upload for rejected {job_id}")
    return Outcome.REJECTED


def _fail(s3, db, bucket, job_id, object_key, attempt, err):
    """Refund a failed job and delete its files, only if the refund is ours to make."""
    message = _error_text(err)
    logger.exception(f"Job {job_id} failed: {message}")
    try:
        refunded = billing.issue_refund(db, job_id, "processing_failed", message, attempt=attempt)
    except Exception:
        logger.exception(f"Could not refund failed {job_id}; its claim will go stale")
        return Outcome.FAILED
    if refunded:
        try:
            deleted = storage.delete_keys(s3, bucket, storage.job_keys(job_id, object_key))
        except Exception:
            logger.exception(f"REFUND_DELETE_FAILED {job_id}")
        else:
            if not deleted:
                logger.error(f"REFUND_DELETE_FAILED {job_id}")
    else:
        logger.warning(f"{job_id}: no refund (claim lost or already settled); files kept")
    return Outcome.FAILED


def handle_job(job_id, video_path, *, s3, db, cfg, roi_masks, keep_alive=None):
    """Analyse one video that SageMaker has already downloaded. Never raises after the claim."""
    state = billing.job_state(db, job_id)
    if state is None:
        logger.warning(f"Skipped {job_id}: no job in the database")
        return Outcome.SKIPPED
    if storage.result_exists(s3, bucket=cfg["aws"]["s3_bucket"], job_id=job_id):
        # A no-op unless an earlier worker crashed between writing the result and settling.
        billing.settle_success(db, job_id)
        logger.info(f"Skipped {job_id}: a result already exists")
        return Outcome.SKIPPED

    attempt = billing.claim(db, job_id)
    if attempt is None:
        state = billing.job_state(db, job_id)
        if state is None or state["status"] in billing.TERMINAL:
            if state is not None and state["status"] == billing.FAILED:
                # A refund deleted the upload; this one landed afterwards (M3a §7b).
                try:
                    s3.delete_object(Bucket=cfg["aws"]["s3_bucket"], Key=state["object_key"])
                except Exception:
                    logger.exception(f"Could not delete the upload for refunded {job_id}")
            logger.info(f"Skipped {job_id}: already finished")
            return Outcome.SKIPPED
        logger.info(f"Skipped {job_id}: another worker holds it")
        return Outcome.SKIPPED

    object_key = state["object_key"]
    research_consent = state["research_consent"]
    try:
        if state["verified_duration_ms"] is None:
            logger.error(f"{job_id}: reached the GPU without being dispatched")
            return _reject(
                s3,
                db,
                cfg["aws"]["s3_bucket"],
                object_key,
                job_id,
                attempt,
                "not_verified",
                "The job was not checked before processing.",
            )
        if keep_alive is None:

            def keep_alive(on_beat):
                def overran():
                    logger.error(
                        f"Job {job_id} overran {cfg['worker']['max_job_minutes']} minutes: "
                        "ending the process so SageMaker replaces this container"
                    )
                    os._exit(1)

                return ClaimKeepAlive(
                    on_beat,
                    cfg["worker"]["heartbeat_seconds"],
                    60 * cfg["worker"]["max_job_minutes"],
                    on_overrun=overran,
                )

        with keep_alive(lambda: billing.touch(db, job_id, attempt)):
            return _analyse(
                cfg["aws"]["s3_bucket"],
                object_key,
                job_id,
                attempt,
                Path(video_path),
                s3=s3,
                db=db,
                roi_masks=roi_masks,
                research_consent=research_consent,
                verified_duration_ms=state["verified_duration_ms"],
            )
    except _ClaimLost:
        logger.warning(f"Lost the claim on {job_id} (attempt {attempt}): money untouched")
        return Outcome.LOST_CLAIM
    except Exception as err:
        return _fail(s3, db, cfg["aws"]["s3_bucket"], job_id, object_key, attempt, err)


def _analyse(
    bucket,
    object_key,
    job_id,
    attempt,
    video_path,
    *,
    s3,
    db,
    roi_masks,
    research_consent,
    verified_duration_ms,
):
    def stage(name):
        if not billing.set_stage(db, job_id, attempt, name):
            raise _ClaimLost

    try:
        duration = media.probe_duration(video_path)
    except media.UnreadableVideo:
        return _reject(
            s3,
            db,
            bucket,
            object_key,
            job_id,
            attempt,
            "unreadable_video",
            "The file is not a readable video.",
        )

    if duration * 1000 > verified_duration_ms + VIDEO_CHANGED_ALLOWANCE_MS:
        return _reject(
            s3,
            db,
            bucket,
            object_key,
            job_id,
            attempt,
            "video_changed",
            "The video was replaced after it was checked.",
        )

    try:
        thumbnail = inference.make_thumbnail(video_path, duration / 2)
    except inference.ThumbnailFailed:
        logger.error(f"Could not make a thumbnail for {job_id}")
        thumbnail = None

    logger.info(f"Analysing {object_key} ({duration:.1f}s)")
    t0 = time.time()
    inference.reset_gpu_peak()
    stage("transcribing")
    events = inference.build_events(video_path)
    stage("inference_full")
    preds_full = inference.predict(events, duration)
    stage("inference_noaudio")
    preds_noaudio = inference.predict(inference.without_audio(events), duration)
    stage("extracting_roi")
    result = extract_response(preds_full, preds_noaudio, roi_masks)
    # Cast and check before any write: an overflowing or non-finite value must fail the job
    # before the dataset copy or the result exists (M3d §5).
    brain = storage.brain_bytes(preds_full)

    result["job_id"] = job_id
    result["processing_time_seconds"] = round(time.time() - t0, 1)
    if inference.fake_mode():
        result["fake_inference"] = True
    gpu = inference.gpu_info()
    if gpu is not None:
        result["gpu"] = gpu

    if research_consent:
        stage("saving_dataset")
        storage.put_dataset(s3, bucket, job_id, object_key, result, brain=brain)

    stage("saving_result")
    if thumbnail is not None:
        storage.put_thumbnail(s3, bucket, job_id, thumbnail)
    storage.put_brain(s3, bucket, job_id, preds_full)
    published = storage.put_result(s3, bucket, job_id, result)
    try:
        settled = billing.settle_success(db, job_id)
    except Exception:
        # The result is public now: nothing refunds or deletes; the error handler settles it.
        logger.exception(f"SETTLE_FAILED {job_id}")
        return Outcome.FAILED
    if not settled:
        logger.warning(f"{job_id} was already settled or refunded; the result is not charged")
    if not published:
        logger.warning(f"Duplicate {job_id}: a result already exists, left unchanged")
        return Outcome.DUPLICATE
    logger.info(f"Done {job_id} in {result['processing_time_seconds']}s")
    return Outcome.DONE


def boot_record(container_start_utc, model_loaded_utc, warmup_done_utc, ready_utc):
    """The container's own start-up times, as UTC text (None for a missing step). Pure."""
    return {
        "container_start_utc": storage.utc_text(container_start_utc)
        if container_start_utc
        else None,
        "model_loaded_utc": storage.utc_text(model_loaded_utc) if model_loaded_utc else None,
        "warmup_done_utc": storage.utc_text(warmup_done_utc) if warmup_done_utc else None,
        "ready_utc": storage.utc_text(ready_utc) if ready_utc else None,
    }


def _put_boot_json(s3, bucket, name, obj):
    """experiments/boots/<name>.json. Never fatal: a missing record is not a failed job."""
    try:
        s3.put_object(
            Bucket=bucket,
            Key=f"experiments/boots/{name}.json",
            Body=json.dumps(obj).encode(),
            ContentType="application/json",
        )
        logger.info(f"Boot record experiments/boots/{name}.json written")
    except Exception:
        logger.exception(f"Could not write boot record {name}")


def _record_boot(s3, bucket, times):
    container_start, model_loaded, warmup_done, ready = times
    record = boot_record(container_start, model_loaded, warmup_done, ready)
    stamp = storage.utc_text(container_start).replace(":", "")
    _put_boot_json(s3, bucket, f"{socket.gethostname()}-{stamp}", record)


class _Handler(BaseHTTPRequestHandler):
    server_version = "NeuroLensWorker/1"

    def log_message(self, format, *args):
        logger.debug(format % args)

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/ping":
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        self.send_error(404)

    def _stream_body(self, path, length):
        remaining = length
        with open(path, "wb") as handle:
            while remaining > 0:
                chunk = self.rfile.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                handle.write(chunk)
                remaining -= len(chunk)
        return length - remaining

    def do_POST(self):
        if self.path != "/invocations":
            self.send_error(404)
            return
        raw = self.headers.get(JOB_HEADER)
        try:
            job_id = str(uuid.UUID(raw)) if raw else None
        except ValueError:
            job_id = None
        if job_id is None:
            logger.warning(f"Rejected an invocation with a bad job id: {raw!r}")
            self._json(400, {"error": "bad_job_id"})
            return

        server = self.server
        if not server.busy.acquire(blocking=False):
            logger.warning("Rejected a second concurrent invocation")
            self._json(503, {"error": "busy"})
            return
        reply = (500, {"job_id": job_id, "outcome": Outcome.FAILED.value})
        try:
            with job_temp_dir(server.feature_cache) as folder:
                # A name never used before on this instance, as the feature cache relies on (§4a).
                path = folder / f"{folder.name}.mp4"
                try:
                    length = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    length = None
                if length is None:
                    logger.warning(f"Invocation for {job_id} has no usable Content-Length")
                else:
                    received = None
                    self.connection.settimeout(BODY_READ_TIMEOUT_S)
                    try:
                        received = self._stream_body(path, length)
                    except TimeoutError:
                        logger.warning(f"Invocation for {job_id} stalled while reading its body")
                        self.close_connection = True  # the rest of the body may still arrive
                    finally:
                        self.connection.settimeout(None)
                    if received is None:
                        pass
                    elif received < length:
                        logger.warning(f"Invocation for {job_id} ended before its Content-Length")
                    else:
                        outcome = handle_job(
                            job_id,
                            str(path),
                            s3=server.s3,
                            db=server.db,
                            cfg=server.cfg,
                            roi_masks=server.roi_masks,
                        )
                        status = 200 if outcome in _OK_OUTCOMES else 500
                        reply = (status, {"job_id": job_id, "outcome": outcome.value})
            # The response waits for the folder to go, so the next request never races its cleanup.
            self._json(*reply)
        except Exception:
            logger.exception(f"Invocation for {job_id} failed")
            self._json(500, {"job_id": job_id, "outcome": Outcome.FAILED.value})
        finally:
            server.busy.release()


_OK_OUTCOMES = (Outcome.DONE, Outcome.DUPLICATE, Outcome.REJECTED, Outcome.SKIPPED)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, *, s3, db, cfg, roi_masks, feature_cache=None):
        self.s3 = s3
        self.db = db
        self.cfg = cfg
        self.roi_masks = roi_masks
        self.feature_cache = feature_cache
        self.busy = threading.Lock()
        super().__init__(address, _Handler)


def make_server(host, port, *, s3, db, cfg, roi_masks, feature_cache=None):
    """The HTTP server SageMaker talks to (port 8080 in production)."""
    return _Server(
        (host, port), s3=s3, db=db, cfg=cfg, roi_masks=roi_masks, feature_cache=feature_cache
    )


def serve():
    """Load, warm up, record the boot and listen. The start-up order is part of the contract."""
    import boto3

    container_start = datetime.now(UTC)
    cfg = settings.load_settings()
    validate_config(cfg)
    aws = cfg["aws"]
    db = dbmod.from_config(cfg)
    # tribev2 writes each job's extracted features here. In the container ./models is the copy of
    # the weights SageMaker downloads, which must stay untouched, so use /tmp. Never empty it
    # while the process runs: exca creates each subfolder once and remembers it.
    feature_cache = tempfile.mkdtemp(prefix="neurolens-features-")
    inference.load_model(cfg, feature_cache=feature_cache)
    model_loaded = datetime.now(UTC)
    roi_masks = inference.roi_masks()

    warmup = settings.resolve_paths(cfg, settings.get_root())["data"] / "warmup.mp4"
    if not warmup.exists():
        raise FileNotFoundError(f"Warm-up clip not found: {warmup}")
    with job_temp_dir(feature_cache):
        inference.build_events(warmup)
    warmup_done = datetime.now(UTC)

    s3 = boto3.client("s3", region_name=aws["region"])
    server = make_server(
        "0.0.0.0",
        PORT,
        s3=s3,
        db=db,
        cfg=cfg,
        roi_masks=roi_masks,
        feature_cache=feature_cache,
    )
    ready = datetime.now(UTC)
    _record_boot(s3, aws["s3_bucket"], (container_start, model_loaded, warmup_done, ready))
    logger.info(f"Worker ready on port {PORT}")
    server.serve_forever()
