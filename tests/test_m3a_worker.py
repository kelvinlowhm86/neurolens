"""M3a worker paths (moto + PostgreSQL, fake inference). Written first from docs/M3a_spec.md §4a,
§5 and §10: each path of handle_record gives the right Outcome and the right money movement, and
process_message deletes the message only for final outcomes (BUSY is not final).

The other §5 paths (oversize, unreadable, too long, a failing record's message, multi-record
deletion, a shutdown mid-job) are checked in the rewritten earlier tests (test_worker.py,
test_m1_hardening.py, test_unreadable_video.py, test_m2b_worker.py, test_m2b_shutdown.py).
Every job here is seeded as the presign endpoint leaves it (conftest `new_job`): the user has
500 cents and the job is `queued` with 90 cents reserved for a 3 s clip.
"""

import contextlib
import json
import threading
import time
import uuid

import pytest
from botocore.exceptions import ClientError
from conftest import USER_ID
from neurolens import billing, inference, storage, worker
from neurolens.worker import Outcome, handle_record, process_message

MODULES = ("neurolens.inference", "neurolens.worker")
STAGE_ORDER = [
    "downloading",
    "transcribing",
    "inference_full",
    "inference_noaudio",
    "extracting_roi",
]
INTERRUPTED = "The job was interrupted. Please upload it again."


@pytest.fixture(autouse=True)
def fake_on(monkeypatch):
    monkeypatch.setenv("FAKE_INFERENCE", "1")


@pytest.fixture(autouse=True)
def no_gpu(patch_everywhere):
    patch_everywhere("gpu_info", lambda: None, *MODULES)


def no_heartbeat(on_beat=None):
    """handle_record's heartbeat factory (M3a §5: heartbeat(on_beat))."""
    return contextlib.nullcontext()


def s3_event(*pairs):
    return json.dumps(
        {
            "Records": [
                {"s3": {"bucket": {"name": b}, "object": {"key": k, "size": 1}}} for b, k in pairs
            ]
        }
    )


def run_record(aws, db, key, cfg, masks, *, s3=None, heartbeat=no_heartbeat):
    return handle_record(
        aws.bucket, key, s3=s3 or aws.s3, db=db, cfg=cfg, roi_masks=masks, heartbeat=heartbeat
    )


def object_exists(aws, key):
    try:
        aws.s3.head_object(Bucket=aws.bucket, Key=key)
    except ClientError as err:
        assert err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound")
        return False
    return True


def never_inference(patch_everywhere):
    def boom(*a, **kw):
        raise AssertionError("inference must not run")

    for name in ("build_events", "without_audio", "predict"):
        patch_everywhere(name, boom, *MODULES)


@pytest.fixture
def received(aws):
    """Send a body and receive it as the worker would: hidden for 120 s, receive count "1"."""

    def put(body):
        aws.sqs.send_message(QueueUrl=aws.queue_url, MessageBody=body)
        resp = aws.sqs.receive_message(
            QueueUrl=aws.queue_url,
            MaxNumberOfMessages=1,
            VisibilityTimeout=120,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
        return resp["Messages"][0]

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


def call_process(aws, db, message, cfg, masks, *, sqs=None, shutdown=None, max_receives=2):
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


def assert_refunded(pg, job_id, reason):
    job = pg.job(job_id)
    assert job["status"] == "failed"
    assert job["error_code"] == reason
    assert pg.refund_reason(job_id) == reason
    assert pg.balance() == (500, 0)


# ---------------------------------------------------------------- 1. unknown job


def test_unknown_job_is_skipped_without_touching_the_upload(
    aws, db, pg, make_cfg, roi_masks_small, new_key, clip_path, spy_s3, patch_everywhere
):
    """No jobs row (a manual `aws s3 cp` or a pre-M3 upload): log and skip, nothing else."""
    job_id, key = new_key()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    never_inference(patch_everywhere)
    s3 = spy_s3(forbid=("download_file", "download_fileobj", "delete_object"))
    outcome = run_record(aws, db, key, make_cfg(), roi_masks_small, s3=s3)
    assert outcome is Outcome.SKIPPED
    assert not [c for c in s3.calls if str(c[2].get("Key", "")).startswith("uploads/")]
    assert object_exists(aws, key)
    assert pg.job(job_id) is None


def test_unknown_job_message_is_deleted(aws, db, make_cfg, roi_masks_small, new_key, received):
    _, key = new_key()
    call_process(aws, db, received(s3_event((aws.bucket, key))), make_cfg(), roi_masks_small)
    assert queue_counts(aws) == 0


# ---------------------------------------------------------------- 2. result already exists


def test_existing_result_settles_a_job_whose_worker_crashed_before_settling(
    aws, db, pg, make_cfg, roi_masks_small, new_job, patch_everywhere
):
    job_id, key = new_job()
    assert billing.claim(db, job_id) == 1  # the earlier worker ...
    storage.put_result(aws.s3, aws.bucket, job_id, {"job_id": job_id, "marker": "earlier"})
    never_inference(patch_everywhere)  # ... wrote its result and crashed before settle_success
    assert run_record(aws, db, key, make_cfg(), roi_masks_small) is Outcome.SKIPPED
    job = pg.job(job_id)
    assert job["status"] == "done" and job["captured_cents"] == 90
    assert pg.balance() == (410, 0)
    body = aws.s3.get_object(Bucket=aws.bucket, Key=f"results/{job_id}.json")["Body"]
    assert json.loads(body.read())["marker"] == "earlier"


@pytest.mark.parametrize("terminal", ["done", "failed"])
def test_existing_result_on_a_terminal_job_changes_nothing(
    aws, db, pg, make_cfg, roi_masks_small, new_job, patch_everywhere, terminal
):
    job_id, key = new_job()
    if terminal == "done":
        billing.settle_success(db, job_id)
    else:
        billing.issue_refund(db, job_id, "upload_not_received", queued_before_s=0)
    storage.put_result(aws.s3, aws.bucket, job_id, {"job_id": job_id})
    before = pg.snapshot()
    never_inference(patch_everywhere)
    assert run_record(aws, db, key, make_cfg(), roi_masks_small) is Outcome.SKIPPED
    assert pg.snapshot() == before


# ---------------------------------------------------------------- 3. claim lost


@pytest.mark.parametrize("terminal", ["done", "failed"])
def test_claim_lost_on_a_finished_job_is_skipped_and_the_message_deleted(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, received, patch_everywhere, terminal
):
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    if terminal == "done":
        billing.settle_success(db, job_id)  # (its result has expired or was never needed here)
    else:
        billing.issue_refund(db, job_id, "unreadable_video", queued_before_s=0)
    before = pg.snapshot()
    never_inference(patch_everywhere)
    assert run_record(aws, db, key, make_cfg(), roi_masks_small) is Outcome.SKIPPED
    call_process(aws, db, received(s3_event((aws.bucket, key))), make_cfg(), roi_masks_small)
    assert queue_counts(aws) == 0
    assert pg.snapshot() == before


def test_claim_lost_on_a_fresh_processing_job_is_busy_and_the_message_is_left_alone(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, received, patch_everywhere
):
    """Another worker holds a fresh claim: neither delete nor release; it reappears after the
    120 s visibility timeout, usually once the job has finished."""
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    assert billing.claim(db, job_id) == 1  # the other worker
    before = pg.snapshot()
    never_inference(patch_everywhere)
    assert run_record(aws, db, key, make_cfg(), roi_masks_small) is Outcome.BUSY

    sqs = RecordingSqs(aws.sqs)
    message = received(s3_event((aws.bucket, key)))
    call_process(aws, db, message, make_cfg(), roi_masks_small, sqs=sqs)
    assert ("delete", None) not in sqs.log
    assert ("visibility", 0) not in sqs.log
    assert queue_counts(aws) == 1 and visible(aws) == []  # still there, still hidden
    assert pg.snapshot() == before


# ---------------------------------------------------------------- 5. upload missing


@pytest.mark.parametrize("code", ["404", "NoSuchKey"])
def test_upload_vanishing_before_the_download_is_refunded_as_missing(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, code, patch_everywhere
):
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)

    class VanishingS3:
        def __init__(self, inner):
            self._inner = inner

        def download_file(self, *a, **kw):
            raise ClientError({"Error": {"Code": code, "Message": "gone"}}, "GetObject")

        def __getattr__(self, name):
            return getattr(self._inner, name)

    never_inference(patch_everywhere)
    outcome = run_record(aws, db, key, make_cfg(), roi_masks_small, s3=VanishingS3(aws.s3))
    assert outcome is Outcome.REJECTED
    assert_refunded(pg, job_id, "upload_missing")


def test_upload_missing_message_is_deleted(
    aws, db, pg, make_cfg, roi_masks_small, new_job, received
):
    job_id, key = new_job()
    call_process(aws, db, received(s3_event((aws.bucket, key))), make_cfg(), roi_masks_small)
    assert queue_counts(aws) == 0
    assert_refunded(pg, job_id, "upload_missing")


# ---------------------------------------------------------------- 6. probe and verify


def test_too_little_credit_for_the_real_length_is_refunded_without_inference(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    """The browser claimed 1 s (90 cents, all the user had); ffprobe measures 61 s (270)."""
    job_id, key = new_job(client_seconds=1, starter_cents=90)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    patch_everywhere("probe_duration", lambda path: 61.0, *MODULES)
    never_inference(patch_everywhere)
    assert run_record(aws, db, key, make_cfg(), roi_masks_small) is Outcome.REJECTED
    assert not object_exists(aws, key)
    job = pg.job(job_id)
    assert job["status"] == "failed"
    assert job["error_code"] == "insufficient_credit_for_actual_duration"
    assert pg.balance() == (90, 0)


def test_a_longer_real_length_with_enough_credit_is_charged_the_real_price(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    job_id, key = new_job(client_seconds=1)  # 90 reserved
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    patch_everywhere("probe_duration", lambda path: 61.0, *MODULES)  # 270
    assert run_record(aws, db, key, make_cfg(), roi_masks_small) is Outcome.DONE
    job = pg.job(job_id)
    assert job["captured_cents"] == 270 and job["verified_duration_ms"] == 61000
    assert pg.balance() == (230, 0)


# ---------------------------------------------------------------- 7-8. success, duplicate


def test_success_is_charged_the_verified_price_with_stages_in_order(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path
):
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    assert run_record(aws, db, key, make_cfg(), roi_masks_small) is Outcome.DONE
    job = pg.job(job_id)
    assert job["status"] == "done"
    assert job["captured_cents"] == 90
    assert job["attempt"] == 1
    assert abs(job["verified_duration_ms"] - round(inference.probe_duration(clip_path) * 1000)) <= 1
    assert [s["stage"] for s in job["stages"]] == STAGE_ORDER
    times = [s["at"] for s in job["stages"]]
    assert times == sorted(times)
    assert pg.balance() == (410, 0)
    assert pg.kinds() == ["starter", "reserve", "capture"]
    body = aws.s3.get_object(Bucket=aws.bucket, Key=f"results/{job_id}.json")["Body"]
    assert json.loads(body.read())["job_id"] == job_id


def test_each_pipeline_step_runs_under_its_own_stage(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    seen = {}

    def current():
        job = pg.job(job_id)
        return job["status"], job["stage"]

    real_build, real_predict = inference.build_events, inference.predict
    from neurolens import engagement

    real_extract = engagement.extract_engagement

    def build(path, *a, **kw):
        seen["build_events"] = current()
        return real_build(path, *a, **kw)

    def predict(events, duration, *a, **kw):
        seen.setdefault("predict", []).append(current())
        return real_predict(events, duration, *a, **kw)

    def extract(*a, **kw):
        seen["extract"] = current()
        return real_extract(*a, **kw)

    patch_everywhere("build_events", build, *MODULES)
    patch_everywhere("predict", predict, *MODULES)
    patch_everywhere("extract_engagement", extract, "neurolens.engagement", "neurolens.worker")

    class StageAtDownload:
        def __init__(self, inner):
            self._inner = inner

        def download_file(self, *a, **kw):
            seen["download"] = current()
            return self._inner.download_file(*a, **kw)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    outcome = run_record(aws, db, key, make_cfg(), roi_masks_small, s3=StageAtDownload(aws.s3))
    assert outcome is Outcome.DONE
    assert seen["download"] == ("processing", "downloading")
    assert seen["build_events"] == ("processing", "transcribing")
    assert seen["predict"] == [
        ("processing", "inference_full"),
        ("processing", "inference_noaudio"),
    ]
    assert seen["extract"] == ("processing", "extracting_roi")


def test_duplicate_is_charged_once_and_leaves_the_other_result(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    """Another worker publishes first (its settlement never ran): this worker's put_result loses
    the conditional write, and its settle_success makes sure the finished job is charged once."""
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    theirs = {"job_id": job_id, "marker": "the other worker"}
    real_predict = inference.predict

    def predict(events, duration, *a, **kw):
        if not storage.result_exists(aws.s3, aws.bucket, job_id):
            assert storage.put_result(aws.s3, aws.bucket, job_id, theirs) is True
        return real_predict(events, duration, *a, **kw)

    patch_everywhere("predict", predict, *MODULES)
    assert run_record(aws, db, key, make_cfg(), roi_masks_small) is Outcome.DUPLICATE
    body = aws.s3.get_object(Bucket=aws.bucket, Key=f"results/{job_id}.json")["Body"]
    assert json.loads(body.read()) == theirs
    job = pg.job(job_id)
    assert job["status"] == "done" and job["captured_cents"] == 90
    assert pg.kinds().count("capture") == 1
    assert pg.balance() == (410, 0)


def test_duplicate_and_done_messages_are_deleted(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, received
):
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    call_process(aws, db, received(s3_event((aws.bucket, key))), make_cfg(), roi_masks_small)
    assert queue_counts(aws) == 0
    assert pg.job(job_id)["status"] == "done"


# ---------------------------------------------------------------- LOST_CLAIM part-way


def test_a_claim_lost_mid_pipeline_stops_with_lost_claim_and_moves_no_money(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    real_build = inference.build_events
    predicted = []

    def build_while_another_worker_reclaims(path, *a, **kw):
        pg.age(job_id, updated_s=120)  # this worker looked dead ...
        assert billing.claim(db, job_id) == 2  # ... and another took the job over
        return real_build(path, *a, **kw)

    patch_everywhere("build_events", build_while_another_worker_reclaims, *MODULES)
    patch_everywhere("predict", lambda *a, **kw: predicted.append(True), *MODULES)
    assert run_record(aws, db, key, make_cfg(), roi_masks_small) is Outcome.LOST_CLAIM
    assert predicted == []  # stopped at the next stage
    job = pg.job(job_id)
    assert job["status"] == "processing" and job["attempt"] == 2
    assert pg.balance() == (410, 90)
    assert not object_exists(aws, f"results/{job_id}.json")


def test_a_job_refunded_by_the_reaper_before_verify_is_a_lost_claim(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    real_probe = inference.probe_duration

    def probe_while_the_reaper_refunds(path):
        pg.age(job_id, updated_s=700)
        assert billing.issue_refund(db, job_id, "stalled", processing_stale_s=600) is True
        return real_probe(path)

    patch_everywhere("probe_duration", probe_while_the_reaper_refunds, *MODULES)
    never_inference(patch_everywhere)
    assert run_record(aws, db, key, make_cfg(), roi_masks_small) is Outcome.LOST_CLAIM
    assert_refunded(pg, job_id, "stalled")  # refunded once, by the reaper alone


def test_lost_claim_message_is_deleted(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, received, patch_everywhere
):
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    real_build = inference.build_events

    def build(path, *a, **kw):
        pg.age(job_id, updated_s=120)
        billing.claim(db, job_id)
        return real_build(path, *a, **kw)

    patch_everywhere("build_events", build, *MODULES)
    call_process(aws, db, received(s3_event((aws.bucket, key))), make_cfg(), roi_masks_small)
    assert queue_counts(aws) == 0


# ---------------------------------------------------------------- failures and shutdown


def test_a_failing_record_is_released_for_retry_with_money_still_reserved(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, received, patch_everywhere
):
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)

    def boom(*a, **kw):
        raise RuntimeError("GPU exploded")

    patch_everywhere("predict", boom, *MODULES)
    with pytest.raises(RuntimeError, match="GPU exploded"):
        run_record(aws, db, key, make_cfg(), roi_masks_small)
    job = pg.job(job_id)
    assert job["status"] == "queued"
    assert "GPU exploded" in job["error_message"]
    assert job["error_code"] is None
    assert pg.balance() == (410, 90)
    assert object_exists(aws, key)  # a failure is not a rejection
    assert billing.claim(db, job_id) == 2  # handed back: the next worker can claim it at once


def test_a_failing_release_for_retry_never_replaces_the_original_error(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere, caplog
):
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)

    def boom(*a, **kw):
        raise RuntimeError("GPU exploded")

    def release_fails(*a, **kw):
        raise ConnectionError("NAT instance down")

    patch_everywhere("predict", boom, *MODULES)
    patch_everywhere("release_for_retry", release_fails, "neurolens.billing", "neurolens.worker")
    with caplog.at_level("DEBUG"):
        with pytest.raises(RuntimeError, match="GPU exploded"):
            run_record(aws, db, key, make_cfg(), roi_masks_small)
    assert any(r.levelname in ("WARNING", "ERROR", "CRITICAL") for r in caplog.records)
    assert pg.job(job_id)["status"] == "processing"  # left to go stale and be re-claimed


def test_the_heartbeat_touches_the_claimed_job(
    aws, db, make_cfg, roi_masks_small, new_job, clip_path, received, patch_everywhere
):
    """§5 step 4: the record runs inside Heartbeat(..., on_beat=touch(db, job_id, attempt))."""
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    touched = []
    real_touch = billing.touch

    def spy(db, job_id, attempt):
        touched.append((job_id, attempt))
        return real_touch(db, job_id, attempt)

    patch_everywhere("touch", spy, "neurolens.billing", "neurolens.worker")
    real_predict = inference.predict

    def slow_predict(*a, **kw):
        time.sleep(0.3)
        return real_predict(*a, **kw)

    patch_everywhere("predict", slow_predict, *MODULES)
    cfg = make_cfg(worker={"heartbeat_seconds": 0.05, "max_job_minutes": 75})
    call_process(aws, db, received(s3_event((aws.bucket, key))), cfg, roi_masks_small)
    assert touched, "no beat touched the job"
    assert set(touched) == {(job_id, 1)}


# ---------------------------------------------------------------- process_message


@pytest.fixture
def fake_record(monkeypatch):
    """Replace handle_record (the M3a §5 signature) with scripted outcomes per key."""

    def install(script):
        seen = []

        def fake(bucket, key, *, s3, db, cfg, roi_masks, heartbeat):
            seen.append((key, db))
            action = script[key]
            if isinstance(action, BaseException):
                raise action
            return action

        monkeypatch.setattr(worker, "handle_record", fake)
        return seen

    return install


def keys(n):
    return [f"uploads/{USER_ID}/{uuid.uuid4()}.mp4" for _ in range(n)]


def test_process_message_passes_the_database_to_each_record(
    aws, make_cfg, roi_masks_small, received, fake_record
):
    db = object()  # process_message only hands it on
    ks = keys(2)
    seen = fake_record(dict.fromkeys(ks, Outcome.DONE))
    call_process(
        aws, db, received(s3_event(*[(aws.bucket, k) for k in ks])), make_cfg(), roi_masks_small
    )
    assert [k for k, _ in seen] == ks
    assert all(d is db for _, d in seen)


@pytest.mark.parametrize("busy_position", [0, 1])
def test_a_busy_record_keeps_the_message_neither_deleted_nor_released(
    aws, make_cfg, roi_masks_small, received, fake_record, busy_position
):
    ks = keys(2)
    script = dict.fromkeys(ks, Outcome.DONE)
    script[ks[busy_position]] = Outcome.BUSY
    fake_record(script)
    sqs = RecordingSqs(aws.sqs)
    message = received(s3_event(*[(aws.bucket, k) for k in ks]))
    call_process(aws, object(), message, make_cfg(), roi_masks_small, sqs=sqs)
    assert sqs.log == []
    assert queue_counts(aws) == 1 and visible(aws) == []
