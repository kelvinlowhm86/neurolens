"""M2b worker behaviour, rewritten for M3a: heartbeat, rejections, failure release, multi-record
messages. Written from docs/M2b_spec.md §1a, §5, §6 and §11, with the M3a §5 signatures (db,
heartbeat(on_beat)) and each record's job seeded in PostgreSQL first (moto, fake inference).

M3a retires the S3 status objects (docs/M3a_spec.md §5, §10): a rejection's plain reason is now the
job's refund reason (`error_code`), a failure hands the job back with `release_for_retry`, and
process_message writes no status. Removed with their Aurora equivalents in tests/test_m3a_worker.py
(§10): the status-object and `stages` tests, "a record that raises on the final attempt leads to
status failed", "a lost conditional write leaves the done status untouched" and "a record whose
result exists is skipped with no status change".

Receive counts: the installed moto (5.2.x) honours MessageSystemAttributeNames, so messages are
received with ApproximateReceiveCount="1" and the attempt is made final or not through
`max_receives` (1 = final, 2 = not final).
"""

import contextlib
import json
import threading
import time
import uuid

import pytest
from conftest import USER_ID
from neurolens import billing, inference, storage, worker
from neurolens.worker import Outcome, handle_record, process_message

MODULES = ("neurolens.inference", "neurolens.worker")
NO_DB = object()  # process_message only hands the database on to (a scripted) handle_record


@pytest.fixture(autouse=True)
def fake_on(monkeypatch):
    monkeypatch.setenv("FAKE_INFERENCE", "1")


@pytest.fixture(autouse=True)
def no_gpu(patch_everywhere):
    patch_everywhere("gpu_info", lambda: None, *MODULES)


def s3_event(*pairs):
    return json.dumps(
        {
            "Records": [
                {"s3": {"bucket": {"name": b}, "object": {"key": k, "size": 1}}} for b, k in pairs
            ]
        }
    )


class TrackingHeartbeat:
    """Stands in for the heartbeat factory handle_record gets: records whether a block is active
    and the on_beat it was given."""

    def __init__(self):
        self.active = False
        self.entered = 0
        self.on_beats = []

    def __call__(self, on_beat=None):
        self.on_beats.append(on_beat)
        return self

    def __enter__(self):
        self.active = True
        self.entered += 1
        return self

    def __exit__(self, *exc):
        self.active = False
        return False


def no_heartbeat(on_beat=None):
    return contextlib.nullcontext()


def never_inference(patch_everywhere):
    def boom(*a, **kw):
        raise AssertionError("inference must not run")

    for name in ("build_events", "without_audio", "predict", "probe_duration"):
        patch_everywhere(name, boom, *MODULES)


def run_record(aws, db, key, cfg, masks, heartbeat=no_heartbeat, s3=None):
    return handle_record(
        aws.bucket, key, s3=s3 or aws.s3, db=db, cfg=cfg, roi_masks=masks, heartbeat=heartbeat
    )


@pytest.fixture
def received(aws):
    """Send a body and receive it as the worker would: hidden for 120 s, with its receive count.

    A message that process_message releases is visible again at once; one it leaves alone stays
    hidden; one it deletes is gone.
    """

    def put(body):
        aws.sqs.send_message(QueueUrl=aws.queue_url, MessageBody=body)
        resp = aws.sqs.receive_message(
            QueueUrl=aws.queue_url,
            MaxNumberOfMessages=1,
            VisibilityTimeout=120,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
        message = resp["Messages"][0]
        assert message["Attributes"]["ApproximateReceiveCount"] == "1"
        return message

    return put


def visible(aws):
    resp = aws.sqs.receive_message(
        QueueUrl=aws.queue_url, MaxNumberOfMessages=10, VisibilityTimeout=0
    )
    return resp.get("Messages", [])


def queue_counts(aws):
    attrs = aws.sqs.get_queue_attributes(
        QueueUrl=aws.queue_url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    return int(attrs["ApproximateNumberOfMessages"]) + int(
        attrs["ApproximateNumberOfMessagesNotVisible"]
    )


class RecordingSqs:
    """Delegates to moto; records change_message_visibility / delete_message calls in order."""

    def __init__(self, inner):
        self._inner = inner
        self.log = []
        self.lock = threading.Lock()

    def change_message_visibility(self, **kwargs):
        with self.lock:
            self.log.append(("visibility", kwargs["VisibilityTimeout"]))
        return self._inner.change_message_visibility(**kwargs)

    def delete_message(self, **kwargs):
        with self.lock:
            self.log.append(("delete", None))
        return self._inner.delete_message(**kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.fixture
def fake_record(monkeypatch):
    """Replace handle_record with a scripted one (the M3a §5 signature). A script value is an
    Outcome to return, an exception to raise, or a callable(heartbeat) run in place of the
    record."""

    def install(script):
        seen = []

        def fake(bucket, key, *, s3, db, cfg, roi_masks, heartbeat):
            seen.append(key)
            action = script[key]
            if isinstance(action, BaseException):
                raise action
            if callable(action) and not isinstance(action, Outcome):
                return action(heartbeat)
            return action

        monkeypatch.setattr(worker, "handle_record", fake)
        return seen

    return install


def call_process(aws, message, cfg, masks, *, max_receives, db=NO_DB, sqs=None, shutdown=None):
    return process_message(
        message,
        s3=aws.s3,
        sqs=sqs or aws.sqs,
        db=db,
        cfg=cfg,
        roi_masks=masks,
        shutdown=shutdown or worker.ShutdownSignal(),
        max_receives=max_receives,
    )


def upload_key():
    return f"uploads/{USER_ID}/{uuid.uuid4()}.mp4"


# ---------------------------------------------------------------- Outcome


def test_outcomes_from_m3a_on():
    """M2b's SKIPPED stays; M3a §5 removes GONE and adds BUSY and LOST_CLAIM."""
    names = set(Outcome.__members__)
    assert {"DONE", "REJECTED", "DUPLICATE", "SKIPPED", "BUSY", "LOST_CLAIM"} <= names
    assert "GONE" not in names
    members = [
        Outcome[n] for n in ("DONE", "REJECTED", "DUPLICATE", "SKIPPED", "BUSY", "LOST_CLAIM")
    ]
    assert len(set(members)) == 6


# ---------------------------------------------------------------- handle_record: heartbeat, order


def test_result_is_written_before_the_job_is_settled(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, monkeypatch
):
    """M2b §4's write order, in Aurora: put_result first, then settle_success (status done)."""
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    at_put_result = []
    real = storage.put_result

    def spy(s3, bucket, jid, result):
        at_put_result.append(pg.job(jid)["status"])
        return real(s3, bucket, jid, result)

    monkeypatch.setattr(storage, "put_result", spy)
    if hasattr(worker, "put_result"):
        monkeypatch.setattr(worker, "put_result", spy)
    assert run_record(aws, db, key, make_cfg(), roi_masks_small) is Outcome.DONE
    assert len(at_put_result) == 1
    assert at_put_result[0] != "done"
    assert pg.job(job_id)["status"] == "done"


def test_record_work_runs_inside_the_heartbeat(
    aws, db, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    _, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    hb = TrackingHeartbeat()
    inside = []
    real_build, real_predict = inference.build_events, inference.predict

    def build(path, *a, **kw):
        inside.append(hb.active)
        return real_build(path, *a, **kw)

    def predict(events, duration, *a, **kw):
        inside.append(hb.active)
        return real_predict(events, duration, *a, **kw)

    patch_everywhere("build_events", build, *MODULES)
    patch_everywhere("predict", predict, *MODULES)
    assert run_record(aws, db, key, make_cfg(), roi_masks_small, heartbeat=hb) is Outcome.DONE
    assert inside == [True, True, True]
    assert hb.active is False  # left before returning
    assert len(hb.on_beats) == 1 and callable(hb.on_beats[0])  # M3a: the touch callback


# ---------------------------------------------------------------- missing uploads (was GONE)


def test_a_notice_for_a_refunded_job_whose_upload_is_gone_changes_nothing(
    aws, db, pg, make_cfg, roi_masks_small, new_job, patch_everywhere
):
    """§4 (M2b): a duplicate notice for a video already rejected (and deleted) must not undo its
    failure. M3a §5: its claim fails, so it is SKIPPED."""
    job_id, key = new_job()
    billing.claim(db, job_id)
    billing.issue_refund(db, job_id, "unreadable_video", "Not a video.", attempt=1)
    before = pg.snapshot()
    never_inference(patch_everywhere)
    assert run_record(aws, db, key, make_cfg(), roi_masks_small) is Outcome.SKIPPED
    assert pg.snapshot() == before


def test_a_missing_upload_on_a_waiting_job_is_refunded(
    aws, db, pg, make_cfg, roi_masks_small, new_job, patch_everywhere
):
    job_id, key = new_job()  # reserved, never uploaded
    never_inference(patch_everywhere)
    assert run_record(aws, db, key, make_cfg(), roi_masks_small) is Outcome.REJECTED
    job = pg.job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "upload_missing"
    assert pg.balance() == (500, 0)


# ---------------------------------------------------------------- rejections are refunded


def assert_rejected(pg, job_id, reason):
    job = pg.job(job_id)
    assert job["status"] == "failed"
    assert job["error_code"] == reason
    assert pg.refund_reason(job_id) == reason
    assert pg.balance() == (500, 0)


def test_oversize_rejection_is_refunded_with_its_reason(
    aws, db, pg, make_cfg, roi_masks_small, new_job
):
    job_id, key = new_job()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"x" * 2000)
    outcome = run_record(aws, db, key, make_cfg(max_upload_bytes=1000), roi_masks_small)
    assert outcome is Outcome.REJECTED
    assert_rejected(pg, job_id, "file_too_large")


def test_too_long_rejection_is_refunded_with_its_reason(
    aws, db, pg, make_cfg, roi_masks_small, new_job, patch_everywhere
):
    patch_everywhere("probe_duration", lambda path: 500.0, *MODULES)
    job_id, key = new_job()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"some bytes")
    outcome = run_record(aws, db, key, make_cfg(), roi_masks_small)  # max 120 s
    assert outcome is Outcome.REJECTED
    assert_rejected(pg, job_id, "duration_exceeds_max_verified")


def test_unreadable_rejection_is_refunded_with_its_reason(
    aws, db, pg, make_cfg, roi_masks_small, new_job
):
    job_id, key = new_job()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"this is not a video")
    outcome = run_record(aws, db, key, make_cfg(), roi_masks_small)
    assert outcome is Outcome.REJECTED
    assert_rejected(pg, job_id, "unreadable_video")


def test_rejection_is_final_even_on_a_first_attempt(
    aws, db, pg, make_cfg, roi_masks_small, new_job, received
):
    """A rejection is always final: refunded, and the message is deleted, with retries left."""
    job_id, key = new_job()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"this is not a video")
    message = received(s3_event((aws.bucket, key)))
    call_process(aws, message, make_cfg(), roi_masks_small, max_receives=2, db=db)
    assert_rejected(pg, job_id, "unreadable_video")
    assert queue_counts(aws) == 0


# ---------------------------------------------------------------- failures and fast release


def failing_record(seconds=0.0, error=None):
    """A record that runs inside its heartbeat for `seconds`, then raises."""

    def run(heartbeat):
        with heartbeat(None):
            time.sleep(seconds)
            raise error or RuntimeError("model crashed")

    return run


@pytest.mark.parametrize("max_receives", [2, 1], ids=["non_final", "final"])
def test_a_failing_record_releases_the_message_and_does_not_delete_it(
    aws, make_cfg, roi_masks_small, received, fake_record, max_receives
):
    key = upload_key()
    fake_record({key: failing_record()})
    message = received(s3_event((aws.bucket, key)))
    call_process(aws, message, make_cfg(), roi_masks_small, max_receives=max_receives)  # returns
    assert len(visible(aws)) == 1  # released: visible again at once (SQS dead-letters it later)


@pytest.mark.parametrize("with_result", [False, True], ids=["no_result", "result_exists"])
def test_process_message_writes_no_status_itself(
    aws, db, pg, make_cfg, roi_masks_small, new_job, received, fake_record, with_result
):
    """M3a §5: the M2b failure and final-attempt status writes are gone (on the final attempt
    the dead-letter handler settles the job instead), with or without a result."""
    job_id, key = new_job()
    if with_result:
        storage.put_result(aws.s3, aws.bucket, job_id, {"job_id": job_id})
    fake_record({key: failing_record()})
    before = pg.snapshot()
    message = received(s3_event((aws.bucket, key)))
    call_process(aws, message, make_cfg(), roi_masks_small, max_receives=1, db=db)
    assert pg.snapshot() == before
    assert len(visible(aws)) == 1


def test_a_failing_real_record_is_released_for_retry_with_a_short_error(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, received, patch_everywhere
):
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    huge = "line one of a long error\n" + "x" * 5000  # a formatted traceback would say "Traceback"

    def boom(*a, **kw):
        raise RuntimeError(huge)

    patch_everywhere("predict", boom, *MODULES)
    call_process(
        aws,
        received(s3_event((aws.bucket, key))),
        make_cfg(),
        roi_masks_small,
        max_receives=2,
        db=db,
    )
    job = pg.job(job_id)
    assert job["status"] == "queued"
    message = job["error_message"]
    assert isinstance(message, str) and message
    assert len(message) <= 200
    assert "Traceback" not in message
    assert pg.balance() == (410, 90)  # money stays reserved for the retry
    assert len(visible(aws)) == 1
    assert not storage.result_exists(aws.s3, aws.bucket, job_id)


def test_release_happens_after_the_last_heartbeat_call_and_no_delete(
    aws, make_cfg, roi_masks_small, received, fake_record
):
    key = upload_key()
    fake_record({key: failing_record(seconds=0.3)})
    cfg = make_cfg(worker={"heartbeat_seconds": 0.03, "max_job_minutes": 75})
    sqs = RecordingSqs(aws.sqs)

    call_process(
        aws, received(s3_event((aws.bucket, key))), cfg, roi_masks_small, max_receives=2, sqs=sqs
    )
    time.sleep(0.2)  # any stray beat would show up here

    with sqs.lock:
        log = list(sqs.log)
    assert ("delete", None) not in log
    beats = [i for i, entry in enumerate(log) if entry == ("visibility", 120)]
    releases = [i for i, entry in enumerate(log) if entry == ("visibility", 0)]
    assert beats, "the record ran inside a heartbeat built from worker.heartbeat_seconds"
    assert releases, "the message was not released"
    assert max(beats) < min(releases)
    assert log[-1] == ("visibility", 0)


# ---------------------------------------------------------------- multi-record messages


def test_multi_record_message_with_all_final_outcomes_is_deleted(
    aws, make_cfg, roi_masks_small, received, fake_record
):
    keys = [upload_key() for _ in range(5)]
    outcomes = [
        Outcome.DONE,
        Outcome.SKIPPED,
        Outcome.DUPLICATE,
        Outcome.REJECTED,
        Outcome.LOST_CLAIM,
    ]
    seen = fake_record(dict(zip(keys, outcomes, strict=True)))
    message = received(s3_event(*[(aws.bucket, k) for k in keys]))
    call_process(aws, message, make_cfg(), roi_masks_small, max_receives=2)
    assert seen == keys
    assert queue_counts(aws) == 0


@pytest.mark.parametrize("raising_position", [0, 1, 2])
def test_multi_record_message_is_not_deleted_when_a_record_fails(
    aws, make_cfg, roi_masks_small, received, fake_record, raising_position
):
    keys = [upload_key() for _ in range(3)]
    script = {k: Outcome.DONE for k in keys}
    script[keys[raising_position]] = failing_record()
    fake_record(script)
    message = received(s3_event(*[(aws.bucket, k) for k in keys]))
    call_process(aws, message, make_cfg(), roi_masks_small, max_receives=2)
    assert len(visible(aws)) == 1  # released, not deleted
