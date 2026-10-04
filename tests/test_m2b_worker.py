"""M2b worker behaviour: status writes, SKIPPED, DUPLICATE, failure status and fast release,
multi-record messages. Written from docs/M2b_spec.md §1a, §4, §5, §6 and §11 (moto, fake
inference).

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
from botocore.exceptions import ClientError
from neurolens import inference, storage, worker
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


def s3_event(*pairs):
    return json.dumps(
        {
            "Records": [
                {"s3": {"bucket": {"name": b}, "object": {"key": k, "size": 1}}} for b, k in pairs
            ]
        }
    )


class TrackingHeartbeat:
    """Stands in for the Heartbeat handle_record gets: records whether a block is active."""

    def __init__(self):
        self.active = False
        self.entered = 0

    def __call__(self):
        return self

    def __enter__(self):
        self.active = True
        self.entered += 1
        return self

    def __exit__(self, *exc):
        self.active = False
        return False


def no_heartbeat():
    return contextlib.nullcontext()


def status_of(aws, job_id):
    try:
        body = aws.s3.get_object(Bucket=aws.bucket, Key=f"status/{job_id}.json")["Body"]
    except ClientError as err:
        assert err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound")
        return None
    return json.loads(body.read())


def result_of(aws, job_id):
    try:
        body = aws.s3.get_object(Bucket=aws.bucket, Key=f"results/{job_id}.json")["Body"]
    except ClientError:
        return None
    return json.loads(body.read())


def never_inference(patch_everywhere):
    def boom(*a, **kw):
        raise AssertionError("inference must not run")

    for name in ("build_events", "without_audio", "predict", "probe_duration"):
        patch_everywhere(name, boom, *MODULES)


def run_record(aws, key, cfg, masks, heartbeat=no_heartbeat, s3=None):
    return handle_record(
        aws.bucket, key, s3=s3 or aws.s3, cfg=cfg, roi_masks=masks, heartbeat=heartbeat
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
    """Replace handle_record with a scripted one (the §1a signature). A script value is an Outcome
    to return, an exception to raise, or a callable(heartbeat) run in place of the record."""

    def install(script):
        seen = []

        def fake(bucket, key, *, s3, cfg, roi_masks, heartbeat):
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


def call_process(aws, message, cfg, masks, *, max_receives, sqs=None, shutdown=None):
    return process_message(
        message,
        s3=aws.s3,
        sqs=sqs or aws.sqs,
        cfg=cfg,
        roi_masks=masks,
        shutdown=shutdown or worker.ShutdownSignal(),
        max_receives=max_receives,
    )


# ---------------------------------------------------------------- Outcome


def test_outcome_gains_skipped():
    assert {"DONE", "REJECTED", "GONE", "DUPLICATE", "SKIPPED"} <= set(Outcome.__members__)
    assert (
        len({Outcome.DONE, Outcome.REJECTED, Outcome.GONE, Outcome.DUPLICATE, Outcome.SKIPPED}) == 5
    )


# ---------------------------------------------------------------- handle_record: status and stages


def test_done_job_ends_with_status_done_and_stages_in_section_4_order(
    aws, make_cfg, roi_masks_small, new_key, clip_path
):
    job_id, key = new_key()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    outcome = run_record(aws, key, make_cfg(), roi_masks_small)
    assert outcome is Outcome.DONE
    status = status_of(aws, job_id)
    assert status["job_id"] == job_id
    assert status["status"] == "done"
    assert status["error"] is None
    assert [s["stage"] for s in status["stages"]] == STAGE_ORDER
    times = [s["at"] for s in status["stages"]]
    assert times == sorted(times)


def test_each_pipeline_step_runs_under_its_own_stage(
    aws, make_cfg, roi_masks_small, new_key, clip_path, patch_everywhere
):
    """§4: processing is written before any real work, and `stage` names the step running."""
    job_id, key = new_key()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    seen = {}

    def current():
        obj = status_of(aws, job_id)
        return None if obj is None else (obj["status"], obj["stage"])

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

    class StatusAtDownload:
        def __init__(self, inner):
            self._inner = inner

        def download_file(self, *a, **kw):
            seen["download"] = current()
            return self._inner.download_file(*a, **kw)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    outcome = run_record(aws, key, make_cfg(), roi_masks_small, s3=StatusAtDownload(aws.s3))

    assert outcome is Outcome.DONE
    assert seen["download"] == ("processing", "downloading")
    assert seen["build_events"] == ("processing", "transcribing")
    assert seen["predict"] == [
        ("processing", "inference_full"),
        ("processing", "inference_noaudio"),
    ]
    assert seen["extract"] == ("processing", "extracting_roi")


def test_result_is_written_before_the_done_status(
    aws, make_cfg, roi_masks_small, new_key, clip_path, monkeypatch
):
    job_id, key = new_key()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    at_put_result = []
    real = storage.put_result

    def spy(s3, bucket, jid, result):
        at_put_result.append(status_of(aws, jid))
        return real(s3, bucket, jid, result)

    monkeypatch.setattr(storage, "put_result", spy)
    if hasattr(worker, "put_result"):
        monkeypatch.setattr(worker, "put_result", spy)
    run_record(aws, key, make_cfg(), roi_masks_small)
    assert len(at_put_result) == 1
    assert at_put_result[0] is None or at_put_result[0]["status"] != "done"
    assert status_of(aws, job_id)["status"] == "done"


def test_record_work_runs_inside_the_heartbeat(
    aws, make_cfg, roi_masks_small, new_key, clip_path, patch_everywhere
):
    job_id, key = new_key()
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
    assert run_record(aws, key, make_cfg(), roi_masks_small, heartbeat=hb) is Outcome.DONE
    assert inside == [True, True, True]
    assert hb.active is False  # left before returning


# ---------------------------------------------------------------- GONE leaves status untouched


def test_gone_upload_leaves_an_existing_failed_status_untouched(
    aws, make_cfg, roi_masks_small, new_key
):
    """§4: a duplicate notice for a video already rejected (and deleted) must not turn its
    failed status back into processing."""
    job_id, key = new_key()
    failed = {
        "job_id": job_id,
        "status": "failed",
        "stage": None,
        "updated_at": "2026-10-14T03:22:10Z",
        "stages": [],
        "error": "The file is not a readable video.",
    }
    aws.s3.put_object(Bucket=aws.bucket, Key=f"status/{job_id}.json", Body=json.dumps(failed))
    outcome = run_record(aws, key, make_cfg(), roi_masks_small)  # the upload does not exist
    assert outcome is Outcome.GONE
    assert status_of(aws, job_id) == failed


def test_gone_upload_writes_no_status_when_there_is_none(aws, make_cfg, roi_masks_small, new_key):
    job_id, key = new_key()
    outcome = run_record(aws, key, make_cfg(), roi_masks_small)
    assert outcome is Outcome.GONE
    assert status_of(aws, job_id) is None


# ---------------------------------------------------------------- SKIPPED / DUPLICATE


@pytest.mark.parametrize("existing_status", [None, "done", "processing"])
def test_existing_result_is_skipped_with_no_inference_and_no_status_change(
    aws, make_cfg, roi_masks_small, new_key, clip_path, patch_everywhere, existing_status
):
    job_id, key = new_key()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    earlier = {"job_id": job_id, "marker": "earlier"}
    storage.put_result(aws.s3, aws.bucket, job_id, earlier)
    if existing_status is not None:
        obj = {
            "job_id": job_id,
            "status": existing_status,
            "stage": None,
            "updated_at": "2026-10-14T03:22:10Z",
            "stages": [],
            "error": None,
        }
        aws.s3.put_object(Bucket=aws.bucket, Key=f"status/{job_id}.json", Body=json.dumps(obj))
    before = status_of(aws, job_id)
    never_inference(patch_everywhere)

    outcome = run_record(aws, key, make_cfg(), roi_masks_small)

    assert outcome is Outcome.SKIPPED
    assert status_of(aws, job_id) == before
    assert result_of(aws, job_id) == earlier


def test_lost_conditional_write_is_duplicate_and_leaves_result_and_done_status(
    aws, make_cfg, roi_masks_small, new_key, clip_path, patch_everywhere
):
    """Another worker finishes the same job while this one runs: its result and done status
    stay exactly as it wrote them."""
    job_id, key = new_key()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    theirs = {"job_id": job_id, "marker": "the other worker"}
    snapshot = {}
    real_predict = inference.predict

    def predict(events, duration, *a, **kw):
        if not snapshot:  # during the first pass, the other worker finishes
            assert storage.put_result(aws.s3, aws.bucket, job_id, theirs) is True
            storage.put_status(aws.s3, aws.bucket, job_id, "done")
            snapshot["status"] = status_of(aws, job_id)
        return real_predict(events, duration, *a, **kw)

    patch_everywhere("predict", predict, *MODULES)

    outcome = run_record(aws, key, make_cfg(), roi_masks_small)

    assert outcome is Outcome.DUPLICATE
    assert result_of(aws, job_id) == theirs
    assert status_of(aws, job_id) == snapshot["status"]
    assert snapshot["status"]["status"] == "done"


# ---------------------------------------------------------------- rejections write failed


def test_oversize_rejection_writes_failed_with_its_plain_reason(
    aws, make_cfg, roi_masks_small, new_key
):
    job_id, key = new_key()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"x" * 2000)
    outcome = run_record(aws, key, make_cfg(max_upload_bytes=1000), roi_masks_small)
    assert outcome is Outcome.REJECTED
    status = status_of(aws, job_id)
    assert status["status"] == "failed"
    assert status["error"] == "The file is larger than the upload limit."


def test_too_long_rejection_writes_failed_with_its_plain_reason(
    aws, make_cfg, roi_masks_small, new_key, patch_everywhere
):
    patch_everywhere("probe_duration", lambda path: 500.0, *MODULES)
    job_id, key = new_key()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"some bytes")
    outcome = run_record(aws, key, make_cfg(), roi_masks_small)  # max 120 s
    assert outcome is Outcome.REJECTED
    status = status_of(aws, job_id)
    assert status["status"] == "failed"
    assert status["error"] == "The video is longer than 120 seconds."


def test_unreadable_rejection_writes_failed_with_its_plain_reason(
    aws, make_cfg, roi_masks_small, new_key
):
    job_id, key = new_key()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"this is not a video")
    outcome = run_record(aws, key, make_cfg(), roi_masks_small)
    assert outcome is Outcome.REJECTED
    status = status_of(aws, job_id)
    assert status["status"] == "failed"
    assert status["error"] == "The file is not a readable video."


def test_rejection_is_final_even_on_a_first_attempt(
    aws, make_cfg, roi_masks_small, new_key, received
):
    """A rejection is always final (§4): failed, and the message is deleted, with retries left."""
    job_id, key = new_key()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"this is not a video")
    message = received(s3_event((aws.bucket, key)))
    call_process(aws, message, make_cfg(), roi_masks_small, max_receives=2)
    assert status_of(aws, job_id)["status"] == "failed"
    assert queue_counts(aws) == 0


# ---------------------------------------------------------------- failures and fast release


def failing_record(seconds=0.0, error=None):
    """A record that runs inside its heartbeat for `seconds`, then raises."""

    def run(heartbeat):
        with heartbeat():
            time.sleep(seconds)
            raise error or RuntimeError("model crashed")

    return run


def check_error_text(error):
    assert isinstance(error, str) and error
    assert len(error) <= 200
    assert "Traceback" not in error


def test_failure_on_a_non_final_attempt_writes_retrying_and_releases(
    aws, make_cfg, roi_masks_small, received, fake_record
):
    job_id = str(uuid.uuid4())
    key = f"uploads/placeholder-user/{job_id}.mp4"
    fake_record({key: failing_record()})
    message = received(s3_event((aws.bucket, key)))

    call_process(aws, message, make_cfg(), roi_masks_small, max_receives=2)  # returns normally

    status = status_of(aws, job_id)
    assert status["status"] == "processing"
    assert status["stage"] == "retrying"
    check_error_text(status["error"])
    assert len(visible(aws)) == 1  # released: visible again at once, not deleted


def test_failure_on_the_final_attempt_writes_failed_and_releases(
    aws, make_cfg, roi_masks_small, received, fake_record
):
    job_id = str(uuid.uuid4())
    key = f"uploads/placeholder-user/{job_id}.mp4"
    fake_record({key: failing_record()})
    message = received(s3_event((aws.bucket, key)))

    call_process(aws, message, make_cfg(), roi_masks_small, max_receives=1)  # receive 1 of 1

    status = status_of(aws, job_id)
    assert status["status"] == "failed"
    check_error_text(status["error"])
    assert len(visible(aws)) == 1  # not deleted: SQS moves it to the dead-letter queue


def test_failure_error_text_is_short_with_no_traceback_even_for_a_huge_message(
    aws, make_cfg, roi_masks_small, received, fake_record
):
    job_id = str(uuid.uuid4())
    key = f"uploads/placeholder-user/{job_id}.mp4"
    huge = "line one of a long error\n" + "x" * 5000  # a formatted traceback would say "Traceback"
    fake_record({key: failing_record(error=RuntimeError(huge))})
    call_process(
        aws, received(s3_event((aws.bucket, key))), make_cfg(), roi_masks_small, max_receives=1
    )
    check_error_text(status_of(aws, job_id)["error"])


@pytest.mark.parametrize("max_receives", [1, 2], ids=["final", "non_final"])
def test_failure_after_a_result_exists_changes_no_status(
    aws, make_cfg, roi_masks_small, received, fake_record, max_receives
):
    """A crash after the result write: the failure status is written only if no result exists."""
    job_id = str(uuid.uuid4())
    key = f"uploads/placeholder-user/{job_id}.mp4"
    storage.put_result(aws.s3, aws.bucket, job_id, {"job_id": job_id})
    fake_record({key: failing_record()})
    call_process(
        aws,
        received(s3_event((aws.bucket, key))),
        make_cfg(),
        roi_masks_small,
        max_receives=max_receives,
    )
    assert status_of(aws, job_id) is None
    assert len(visible(aws)) == 1


def test_release_happens_after_the_last_heartbeat_call_and_no_delete(
    aws, make_cfg, roi_masks_small, received, fake_record
):
    job_id = str(uuid.uuid4())
    key = f"uploads/placeholder-user/{job_id}.mp4"
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


def test_end_to_end_inference_failure_releases_and_reports_retrying(
    aws, make_cfg, roi_masks_small, new_key, clip_path, received, patch_everywhere
):
    def boom(*a, **kw):
        raise RuntimeError("GPU exploded")

    patch_everywhere("predict", boom, *MODULES)
    job_id, key = new_key()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    call_process(
        aws, received(s3_event((aws.bucket, key))), make_cfg(), roi_masks_small, max_receives=2
    )
    status = status_of(aws, job_id)
    assert status["status"] == "processing"
    assert status["stage"] == "retrying"
    assert len(visible(aws)) == 1
    assert result_of(aws, job_id) is None


# ---------------------------------------------------------------- multi-record messages


def test_multi_record_message_with_all_final_outcomes_is_deleted(
    aws, make_cfg, roi_masks_small, received, fake_record
):
    keys = [f"uploads/placeholder-user/{uuid.uuid4()}.mp4" for _ in range(5)]
    outcomes = [Outcome.DONE, Outcome.SKIPPED, Outcome.DUPLICATE, Outcome.GONE, Outcome.REJECTED]
    seen = fake_record(dict(zip(keys, outcomes, strict=True)))
    message = received(s3_event(*[(aws.bucket, k) for k in keys]))
    call_process(aws, message, make_cfg(), roi_masks_small, max_receives=2)
    assert seen == keys
    assert queue_counts(aws) == 0


@pytest.mark.parametrize("raising_position", [0, 1, 2])
def test_multi_record_message_is_not_deleted_when_a_record_fails(
    aws, make_cfg, roi_masks_small, received, fake_record, raising_position
):
    keys = [f"uploads/placeholder-user/{uuid.uuid4()}.mp4" for _ in range(3)]
    script = {k: Outcome.DONE for k in keys}
    script[keys[raising_position]] = failing_record()
    fake_record(script)
    message = received(s3_event(*[(aws.bucket, k) for k in keys]))
    call_process(aws, message, make_cfg(), roi_masks_small, max_receives=2)
    assert len(visible(aws)) == 1  # released, not deleted
