"""M1 hardening tests. Written from docs/M1_spec.md sections 1, 5 and 6a.

Covers: config validation, delete failure, GONE outcome, unparseable messages, poll_once,
audio-only videos and subprocess timeouts. Calls use the M2b §1a signatures (heartbeat,
shutdown, max_receives); the behaviour checked is unchanged.
"""

import contextlib
import json
import re
import subprocess
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from neurolens import inference, settings, worker
from neurolens.worker import Outcome, handle_record, process_message

MODULES = ("neurolens.inference", "neurolens.worker")
MAX_RECEIVES = 2  # the job queue's maxReceiveCount (M2a §4h); every message here is receive 1


def no_heartbeat():
    """M2b §1a: handle_record's heartbeat factory. These tests do not exercise the heartbeat."""
    return contextlib.nullcontext()


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

    patch_everywhere("build_events", boom, *MODULES)
    patch_everywhere("without_audio", boom, *MODULES)
    patch_everywhere("predict", boom, *MODULES)


def client_error(code, operation="GetObject"):
    return ClientError({"Error": {"Code": code, "Message": "test"}}, operation)


class Wrapped:
    """Delegates everything to a boto3 client except the methods given in `overrides`."""

    def __init__(self, inner, **overrides):
        self._inner = inner
        self._overrides = overrides

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._inner, name)


def raiser(exc):
    def fn(*args, **kwargs):
        raise exc

    return fn


@pytest.fixture(scope="session")
def audio_only_path(tmp_path_factory):
    """An mp4 with an audio stream and no video stream."""
    path = tmp_path_factory.mktemp("audio_only") / "audio_only.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=3",
    ]
    subprocess.run(cmd + [str(path)], check=True, capture_output=True)
    return path


# ---------------------------------------------------------------- Outcome.GONE


def test_outcome_has_gone():
    assert "GONE" in Outcome.__members__
    assert Outcome.GONE not in (Outcome.DONE, Outcome.REJECTED)


# ---------------------------------------------------------------- 3. delete failure


def test_failed_message_delete_is_logged_not_raised_and_message_stays(
    aws, make_cfg, roi_masks_small, new_key, queue_message, remaining, caplog
):
    cfg = make_cfg(max_upload_bytes=1000)
    _, key = new_key()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"x" * 2000)  # REJECTED, then delete
    sqs = Wrapped(aws.sqs, delete_message=raiser(client_error("ServiceUnavailable", "Delete")))
    message = queue_message(s3_event((aws.bucket, key)))
    with caplog.at_level("DEBUG"):
        process_message(
            message,
            s3=aws.s3,
            sqs=sqs,
            cfg=cfg,
            roi_masks=roi_masks_small,
            shutdown=worker.ShutdownSignal(),
            max_receives=MAX_RECEIVES,
        )
    assert len(remaining()) == 1
    assert any(r.levelname in ("ERROR", "CRITICAL") for r in caplog.records)


# ---------------------------------------------------------------- 4a. run() validates config

MISSING_KEYS = [
    ("aws", "region"),
    ("aws", "s3_bucket"),
    ("aws", "sqs_queue_url"),
    ("paths", "output"),
    (None, "max_upload_bytes"),
]


@pytest.mark.parametrize("section, leaf", MISSING_KEYS, ids=[k[1] for k in MISSING_KEYS])
def test_run_rejects_a_config_with_a_missing_key_before_loading_the_model(
    make_cfg, monkeypatch, section, leaf
):
    cfg = make_cfg()
    if section is None:
        del cfg[leaf]
    else:
        del cfg[section][leaf]
    loaded = []

    class ModelLoadedTooEarly(BaseException):
        pass

    def load_model(*a, **kw):
        loaded.append(True)
        raise ModelLoadedTooEarly

    monkeypatch.setattr(settings, "load_config", lambda *a, **kw: cfg)
    monkeypatch.setattr(inference, "load_model", load_model)
    if hasattr(worker, "load_model"):
        monkeypatch.setattr(worker, "load_model", load_model)
    # should validation be missing, never enter the forever loop
    monkeypatch.setattr(worker, "poll_once", raiser(ModelLoadedTooEarly()), raising=False)

    with pytest.raises(Exception, match=re.escape(leaf)):
        worker.run()
    assert loaded == []


# ---------------------------------------------------------------- 4b. output folder before download


def test_unusable_output_folder_fails_before_the_download(
    aws, make_cfg, roi_masks_small, new_key, clip_path, tmp_path, spy_s3, patch_everywhere
):
    blocker = tmp_path / "a_regular_file"
    blocker.write_text("not a folder")
    cfg = make_cfg(paths={**make_cfg()["paths"], "output": str(blocker / "results")})
    _, key = new_key()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    never_inference(patch_everywhere)
    s3 = spy_s3(forbid=("download_file", "download_fileobj"), forbid_on_uploads=("get_object",))

    with pytest.raises(Exception):  # noqa: B017 - the exact type is not specified
        handle_record(
            aws.bucket, key, s3=s3, cfg=cfg, roi_masks=roi_masks_small, heartbeat=no_heartbeat
        )

    assert "download_file" not in s3.names()
    assert object_exists(aws, key)  # a failure is not a rejection


# ---------------------------------------------------------------- A. audio-only video


def test_probe_duration_raises_for_an_audio_only_file(audio_only_path):
    with pytest.raises(inference.UnreadableVideo):
        inference.probe_duration(audio_only_path)


def test_probe_duration_still_accepts_a_normal_clip(clip_path):
    assert inference.probe_duration(clip_path) == pytest.approx(3.0, abs=0.5)


def test_audio_only_upload_is_rejected_and_deleted_without_inference(
    aws, make_cfg, roi_masks_small, new_key, audio_only_path, patch_everywhere
):
    cfg = make_cfg()
    job_id, key = new_key()
    aws.s3.upload_file(str(audio_only_path), aws.bucket, key)
    never_inference(patch_everywhere)

    outcome = handle_record(
        aws.bucket, key, s3=aws.s3, cfg=cfg, roi_masks=roi_masks_small, heartbeat=no_heartbeat
    )

    assert outcome is Outcome.REJECTED
    assert not object_exists(aws, key)
    assert not (Path(cfg["paths"]["output"]) / f"{job_id}.json").exists()


def test_normal_video_still_passes_after_the_audio_only_rule(
    aws, make_cfg, roi_masks_small, new_key, clip_path
):
    _, key = new_key()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    outcome = handle_record(
        aws.bucket,
        key,
        s3=aws.s3,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        heartbeat=no_heartbeat,
    )
    assert outcome is Outcome.DONE


# ---------------------------------------------------------------- B. GONE


def test_missing_object_is_gone_and_nothing_else_happens(
    aws, make_cfg, roi_masks_small, new_key, spy_s3, patch_everywhere
):
    cfg = make_cfg()
    job_id, key = new_key()
    never_inference(patch_everywhere)
    s3 = spy_s3(
        forbid=("download_file", "download_fileobj", "delete_object"),
        forbid_on_uploads=("get_object",),
    )

    outcome = handle_record(
        aws.bucket, key, s3=s3, cfg=cfg, roi_masks=roi_masks_small, heartbeat=no_heartbeat
    )

    assert outcome is Outcome.GONE
    assert not (Path(cfg["paths"]["output"]) / f"{job_id}.json").exists()


def test_duplicate_event_for_an_oversize_object_is_rejected_then_gone(
    aws, make_cfg, roi_masks_small, new_key
):
    cfg = make_cfg(max_upload_bytes=1000)
    _, key = new_key()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"x" * 2000)

    first = handle_record(
        aws.bucket, key, s3=aws.s3, cfg=cfg, roi_masks=roi_masks_small, heartbeat=no_heartbeat
    )
    assert first is Outcome.REJECTED
    assert not object_exists(aws, key)

    second = handle_record(
        aws.bucket, key, s3=aws.s3, cfg=cfg, roi_masks=roi_masks_small, heartbeat=no_heartbeat
    )
    assert second is Outcome.GONE


def test_message_for_a_gone_object_is_deleted(
    aws, make_cfg, roi_masks_small, new_key, queue_message, remaining
):
    _, key = new_key()
    process_message(
        queue_message(s3_event((aws.bucket, key))),
        s3=aws.s3,
        sqs=aws.sqs,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        shutdown=worker.ShutdownSignal(),
        max_receives=MAX_RECEIVES,
    )
    assert remaining() == []


@pytest.mark.parametrize("code", ["404", "NoSuchKey"])
def test_download_reporting_not_found_is_gone(
    aws, make_cfg, roi_masks_small, new_key, clip_path, code
):
    """The object vanishes between head_object and download_file (for example it expired)."""
    _, key = new_key()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    s3 = Wrapped(aws.s3, download_file=raiser(client_error(code)))
    outcome = handle_record(
        aws.bucket, key, s3=s3, cfg=make_cfg(), roi_masks=roi_masks_small, heartbeat=no_heartbeat
    )
    assert outcome is Outcome.GONE


@pytest.mark.parametrize("code", ["AccessDenied", "500"])
def test_download_with_any_other_error_still_raises_and_keeps_the_message(
    aws, make_cfg, roi_masks_small, new_key, clip_path, queue_message, remaining, code
):
    _, key = new_key()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    s3 = Wrapped(aws.s3, download_file=raiser(client_error(code)))

    with pytest.raises(ClientError):
        handle_record(
            aws.bucket,
            key,
            s3=s3,
            cfg=make_cfg(),
            roi_masks=roi_masks_small,
            heartbeat=no_heartbeat,
        )

    process_message(
        queue_message(s3_event((aws.bucket, key))),
        s3=s3,
        sqs=aws.sqs,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        shutdown=worker.ShutdownSignal(),
        max_receives=MAX_RECEIVES,
    )
    assert len(remaining()) == 1
    assert object_exists(aws, key)


# ---------------------------------------------------------------- C. unparseable message body

BAD_BODIES = [
    "not json",
    json.dumps({"Records": [{}]}),
    json.dumps({"Records": [{"s3": {"bucket": {}, "object": {"key": "uploads/u/a.mp4"}}}]}),
    json.dumps({"Records": [{"s3": {"bucket": {"name": "b"}, "object": {}}}]}),
    json.dumps({"Records": [{"s3": {}}]}),
]


@pytest.mark.parametrize("body", BAD_BODIES, ids=range(len(BAD_BODIES)))
def test_unparseable_message_is_deleted_without_calling_handle_record(
    aws, make_cfg, roi_masks_small, queue_message, remaining, monkeypatch, body
):
    def fail(*a, **kw):
        raise AssertionError("handle_record must not be called")

    monkeypatch.setattr(worker, "handle_record", fail)
    process_message(
        queue_message(body),
        s3=aws.s3,
        sqs=aws.sqs,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        shutdown=worker.ShutdownSignal(),
        max_receives=MAX_RECEIVES,
    )
    assert remaining() == []


# ---------------------------------------------------------------- D. poll_once


class ShortPollSqs:
    """Delegates to moto but never waits 20 s, and makes unhandled messages visible at once."""

    def __init__(self, inner, receive=None):
        self._inner = inner
        self._receive = receive

    def receive_message(self, **kwargs):
        if self._receive is not None:
            return self._receive(**kwargs)
        kwargs.update(WaitTimeSeconds=0, VisibilityTimeout=0)
        return self._inner.receive_message(**kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_poll_once_processes_a_waiting_message(
    aws, make_cfg, roi_masks_small, new_key, clip_path, remaining
):
    cfg = make_cfg()
    job_id, key = new_key()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    aws.sqs.send_message(QueueUrl=aws.queue_url, MessageBody=s3_event((aws.bucket, key)))

    ok = worker.poll_once(
        s3=aws.s3,
        sqs=ShortPollSqs(aws.sqs),
        cfg=cfg,
        roi_masks=roi_masks_small,
        shutdown=worker.ShutdownSignal(),
        max_receives=MAX_RECEIVES,
    )

    assert ok is True
    body = aws.s3.get_object(Bucket=aws.bucket, Key=f"results/{job_id}.json")["Body"]
    assert json.loads(body.read())["job_id"] == job_id  # M2a: the result is published to S3
    assert remaining() == []


def test_poll_once_with_an_empty_queue_returns_true(aws, make_cfg, roi_masks_small):
    ok = worker.poll_once(
        s3=aws.s3,
        sqs=ShortPollSqs(aws.sqs),
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        shutdown=worker.ShutdownSignal(),
        max_receives=MAX_RECEIVES,
    )
    assert ok is True


def test_poll_once_survives_a_failing_receive_message(aws, make_cfg, roi_masks_small):
    sqs = ShortPollSqs(aws.sqs, receive=raiser(client_error("ServiceUnavailable", "Receive")))
    ok = worker.poll_once(
        s3=aws.s3,
        sqs=sqs,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        shutdown=worker.ShutdownSignal(),
        max_receives=MAX_RECEIVES,
    )
    assert ok is False


def test_poll_once_asks_for_one_message_with_a_long_poll(aws, make_cfg, roi_masks_small):
    seen = []

    def receive(**kwargs):
        seen.append(kwargs)
        return {}

    sqs = ShortPollSqs(aws.sqs, receive=receive)
    ok = worker.poll_once(
        s3=aws.s3,
        sqs=sqs,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        shutdown=worker.ShutdownSignal(),
        max_receives=MAX_RECEIVES,
    )
    assert ok is True
    assert len(seen) == 1
    assert seen[0]["QueueUrl"] == aws.queue_url
    assert seen[0]["MaxNumberOfMessages"] == 1
    assert seen[0]["WaitTimeSeconds"] == 20


# ---------------------------------------------------------------- E. timeouts


@pytest.fixture
def run_spy(monkeypatch):
    """Records every subprocess.run call made by neurolens.inference, then runs it for real."""
    calls = []
    real = subprocess.run

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(inference.subprocess, "run", spy)
    return calls


def test_probe_duration_runs_ffprobe_with_a_30_second_timeout(clip_path, run_spy):
    inference.probe_duration(clip_path)
    assert run_spy
    assert all(kwargs.get("timeout") == 30 for _, kwargs in run_spy)


def test_probe_duration_timeout_is_an_unreadable_video(clip_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=30)

    monkeypatch.setattr(inference.subprocess, "run", timeout)
    with pytest.raises(inference.UnreadableVideo):
        inference.probe_duration(clip_path)
