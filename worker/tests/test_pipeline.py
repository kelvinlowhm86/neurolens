"""
Whole jobs through the real pipeline + Postgres, with an in-memory S3/SQS and the mock model.
"""

import json
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from neurolens_common import jobs, ledger, s3keys
from neurolens_common.config import Settings
from neurolens_common.jobs import JobStatus
from neurolens_common.schemas import ResultV2
from neurolens_common.testing import assert_ledger_consistent
from neurolens_worker import media
from neurolens_worker.inference.mock import MockBackend
from neurolens_worker.jobqueue import JobQueue
from neurolens_worker.lifecycle import Lifecycle
from neurolens_worker.metrics import Metrics
from neurolens_worker.pipeline import JobRunner

BUCKET = "test-bucket"


class FakeS3:
    def __init__(self, root: Path):
        self.root = root

    def upload(self, key: str, src: Path):
        dst = self.root / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)

    def download_file(self, bucket, key, filename):
        shutil.copy(self.root / key, filename)

    def put_object(self, Bucket, Key, Body, ContentType):
        dst = self.root / Key
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(Body)


class FakeSQS:
    """Just enough of the SQS API for JobQueue."""

    def __init__(self):
        self.messages: dict[str, dict] = {}     # receipt → message
        self.sent: list[dict] = []
        self.visibility: dict[str, int] = {}

    def add(self, body: str) -> str:
        receipt = str(uuid.uuid4())
        self.messages[receipt] = {"MessageId": receipt, "ReceiptHandle": receipt, "Body": body,
                                  "Attributes": {"ApproximateReceiveCount": "1"}}
        return receipt

    def receive_message(self, **kw):
        return {"Messages": list(self.messages.values())[:1]}

    def delete_message(self, QueueUrl, ReceiptHandle):
        self.messages.pop(ReceiptHandle, None)

    def change_message_visibility(self, QueueUrl, ReceiptHandle, VisibilityTimeout):
        self.visibility[ReceiptHandle] = VisibilityTimeout

    def send_message(self, QueueUrl, MessageBody, MessageAttributes=None):
        self.sent.append({"Body": MessageBody, "MessageAttributes": MessageAttributes})

    def get_queue_attributes(self, QueueUrl, AttributeNames):
        return {"Attributes": {"ApproximateNumberOfMessages": str(len(self.messages))}}


def s3_event(key: str) -> str:
    return json.dumps({"Records": [{
        "eventName": "ObjectCreated:Post", "eventTime": datetime.now(timezone.utc).isoformat(),
        "s3": {"bucket": {"name": BUCKET}, "object": {"key": key, "size": 1}},
    }]})


@pytest.fixture
def clip5(tmp_path_factory) -> Path:
    return media.make_test_clip(tmp_path_factory.mktemp("clips") / "five.mp4", seconds=5)


@pytest.fixture
def env(db_url, conn, make_user, tmp_path):
    cfg = Settings(database_url=db_url, scratch_dir=tmp_path / "scratch", mock_seconds_per_pass=0,
                   sqs_queue_url="fake", heartbeat_seconds=1)
    s3, sqs = FakeS3(tmp_path / "s3"), FakeSQS()
    backend = MockBackend(seconds_per_pass=0)
    backend.load()
    interrupted = threading.Event()
    runner = JobRunner(cfg, backend, s3, JobQueue(sqs, "fake"), Lifecycle(cfg), Metrics(), interrupted)
    runner.lc.register()

    def submit(src: Path | None, credits_held: int = 30, balance: int = 100, name: str = "input.mp4"):
        uid = make_user(balance)
        job_id = uuid.uuid4()
        key = s3keys.input_key(uid, job_id, Path(name).suffix)
        jobs.create_job(conn, job_id=job_id, user_id=uid, filename="ad.mp4", content_type="video/mp4",
                        size_bytes=1, s3_input_key=key, est_duration_s=5)
        ledger.reserve(conn, job_id, credits_held)
        if src is not None:
            s3.upload(key, src)
        receipt = sqs.add(s3_event(key))
        return uid, job_id, receipt

    class Env:
        pass

    e = Env()
    e.cfg, e.s3, e.sqs, e.runner, e.backend, e.interrupted, e.submit, e.conn = (
        cfg, s3, sqs, runner, backend, interrupted, submit, conn)
    e.run_one = lambda: runner.handle(runner.queue.receive(wait_s=0))
    return e


def _job(conn, job_id):
    return jobs.get_job(conn, job_id)


def test_happy_path(env, clip5):
    uid, job_id, _ = env.submit(clip5, credits_held=30)
    env.run_one()
    j = _job(env.conn, job_id)
    assert j["status"] == JobStatus.SUCCEEDED
    assert j["credits_captured"] == 5 and float(j["actual_duration_s"]) == pytest.approx(5, abs=0.1)
    for col in ("claimed_at", "downloaded_at", "validated_at", "pass_full_done_at",
                "pass_silent_done_at", "roi_done_at", "completed_at"):
        assert j[col] is not None, col
    assert j["cold_start"] is True
    result = ResultV2.model_validate_json((env.s3.root / j["s3_result_key"]).read_text())
    assert result.duration_seconds == 5 and len(result.timesteps) == 5
    assert env.sqs.messages == {}                                     # message deleted
    assert ledger.balance(env.conn, uid) == {"credits_available": 95, "credits_reserved": 0}
    assert_ledger_consistent(env.conn, uid)
    j = _job(env.conn, job_id)                                        # step after TRIBE: AI report
    assert (j["report_status"], j["report_model"]) == ("READY", "mock")
    assert (env.s3.root / j["s3_report_key"]).read_bytes().startswith(b"%PDF")


def test_report_failure_does_not_fail_the_job(env, clip5, monkeypatch):
    import neurolens_worker.pipeline as pipeline_mod

    def broken(*a, **k):
        raise RuntimeError("LLM unavailable")
    monkeypatch.setattr(pipeline_mod, "generate_report", broken)
    _, job_id, _ = env.submit(clip5)
    env.run_one()
    j = _job(env.conn, job_id)
    assert j["status"] == JobStatus.SUCCEEDED
    assert j["report_status"] == "FAILED" and "LLM unavailable" in j["report_error"]


def test_report_off_skips_the_step(env, clip5):
    env.runner.cfg = env.cfg.model_copy(update={"report_llm": "off"})
    _, job_id, _ = env.submit(clip5)
    env.run_one()
    j = _job(env.conn, job_id)
    assert j["status"] == JobStatus.SUCCEEDED and j["report_status"] == "OFF"


def test_second_job_is_warm(env, clip5):
    env.submit(clip5)
    env.run_one()
    _, job2, _ = env.submit(clip5)
    env.run_one()
    assert _job(env.conn, job2)["cold_start"] is False


def test_too_long_is_refunded(env, clip5):
    env.runner.cfg = env.cfg.model_copy(update={"max_video_seconds": 3})
    uid, job_id, _ = env.submit(clip5)
    env.run_one()
    j = _job(env.conn, job_id)
    assert (j["status"], j["error_code"]) == (JobStatus.FAILED, "TOO_LONG")
    assert env.sqs.messages == {}
    assert ledger.balance(env.conn, uid) == {"credits_available": 100, "credits_reserved": 0}


def test_not_enough_credits_for_real_duration(env, tmp_path):
    clip = media.make_test_clip(tmp_path / "eight.mp4", seconds=8)
    uid, job_id, _ = env.submit(clip, credits_held=5, balance=6)    # needs 8, can only reach 6
    env.run_one()
    j = _job(env.conn, job_id)
    assert (j["status"], j["error_code"]) == (JobStatus.FAILED, "INSUFFICIENT_CREDITS")
    assert ledger.balance(env.conn, uid) == {"credits_available": 6, "credits_reserved": 0}


def test_not_a_video(env, tmp_path):
    junk = tmp_path / "junk.mp4"
    junk.write_text("definitely not a video")
    uid, job_id, _ = env.submit(junk)
    env.run_one()
    assert _job(env.conn, job_id)["error_code"] == "INVALID_MEDIA"
    assert ledger.balance(env.conn, uid)["credits_reserved"] == 0


def test_transient_error_leaves_job_for_retry(env, clip5, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("CUDA hiccup")
    monkeypatch.setattr(env.backend, "predict_full", boom)
    uid, job_id, receipt = env.submit(clip5)
    env.run_one()
    j = _job(env.conn, job_id)
    assert j["status"] == JobStatus.UPLOADED and j["worker_id"] is None and "CUDA hiccup" in j["error_message"]
    assert receipt in env.sqs.messages and env.sqs.visibility[receipt] == 30   # visible again soon
    assert ledger.balance(env.conn, uid)["credits_reserved"] == 5              # held, resized to real duration
    monkeypatch.undo()
    env.run_one()                                                              # the retry claims it at once
    j = _job(env.conn, job_id)
    assert j["status"] == JobStatus.SUCCEEDED and j["attempts"] == 2


def test_spot_interruption_requeues_without_using_a_retry(env, clip5):
    env.interrupted.set()
    uid, job_id, receipt = env.submit(clip5)
    env.run_one()
    j = _job(env.conn, job_id)
    assert j["status"] == JobStatus.UPLOADED and j["worker_id"] is None
    assert receipt not in env.sqs.messages                                     # original deleted
    assert env.sqs.sent and env.sqs.sent[0]["MessageAttributes"]["requeue_reason"]["StringValue"] == "spot"
    assert ledger.balance(env.conn, uid)["credits_reserved"] == 30


def test_duplicate_message_after_success_is_dropped(env, clip5):
    _, job_id, _ = env.submit(clip5)
    env.run_one()
    key = _job(env.conn, job_id)["s3_input_key"]
    env.sqs.add(s3_event(key))                                                # SQS at-least-once redelivery
    env.run_one()
    assert env.sqs.messages == {}
    assert _job(env.conn, job_id)["attempts"] == 1


def test_s3_test_event_is_deleted(env):
    env.sqs.add(json.dumps({"Event": "s3:TestEvent"}))
    env.run_one()
    assert env.sqs.messages == {}
