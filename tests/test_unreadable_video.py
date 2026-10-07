"""The unreadable-video rule. Written from docs/M1_spec.md sections 1, 5 and 6a.

ffprobe cannot measure a file that is not a video, so probe_duration raises UnreadableVideo
and the worker rejects the upload (deletes it and its message) instead of retrying forever.
Calls use the M3a §5 signatures (db, heartbeat(on_beat)), with each record's job seeded in
PostgreSQL first; the behaviour checked is unchanged (M3a also refunds it: `unreadable_video`).
"""

import contextlib
import json
import os
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from neurolens import inference, worker
from neurolens.worker import Outcome, handle_record, process_message

MODULES = ("neurolens.inference", "neurolens.worker")
NOT_A_VIDEO = b"this is not a video"
MAX_RECEIVES = 2  # the job queue's maxReceiveCount (M2a §4h); every message here is receive 1


def no_heartbeat(on_beat=None):
    """handle_record's heartbeat factory (M3a §5: heartbeat(on_beat)). Not exercised here."""
    return contextlib.nullcontext()


@pytest.fixture(autouse=True)
def fake_on(monkeypatch):
    monkeypatch.setenv("FAKE_INFERENCE", "1")


@pytest.fixture(autouse=True)
def no_gpu(patch_everywhere):
    patch_everywhere("gpu_info", lambda: None, *MODULES)


@pytest.fixture
def inference_calls(patch_everywhere):
    """Records any call to build_events, without_audio or predict (none may happen)."""
    log = {"build_events": [], "without_audio": [], "predict": []}

    def refuse(name):
        def fn(*a, **kw):
            log[name].append(a)
            raise AssertionError(f"{name} must not be called for an unreadable video")

        return fn

    for name in log:
        patch_everywhere(name, refuse(name), *MODULES)
    return log


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


# ---------------------------------------------------------------- probe_duration


def test_unreadable_video_is_a_value_error_subclass():
    assert issubclass(inference.UnreadableVideo, ValueError)


@pytest.mark.parametrize(
    "content",
    [NOT_A_VIDEO, b"", os.urandom(4096)],
    ids=["plain_text", "empty_file", "random_bytes"],
)
def test_probe_duration_raises_unreadable_video(tmp_path, content):
    path = tmp_path / "bad.mp4"
    path.write_bytes(content)
    with pytest.raises(inference.UnreadableVideo):
        inference.probe_duration(path)


def test_probe_duration_still_measures_a_real_clip(clip_path):
    duration = inference.probe_duration(clip_path)
    assert isinstance(duration, float)
    assert duration == pytest.approx(3.0, abs=0.5)


# ---------------------------------------------------------------- handle_record


def test_unreadable_upload_is_rejected_deleted_and_not_inferred(
    aws, make_cfg, roi_masks_small, db, new_job, inference_calls
):
    cfg = make_cfg()
    job_id, key = new_job()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=NOT_A_VIDEO)

    outcome = handle_record(
        aws.bucket,
        key,
        s3=aws.s3,
        db=db,
        cfg=cfg,
        roi_masks=roi_masks_small,
        heartbeat=no_heartbeat,
    )

    assert outcome is Outcome.REJECTED
    assert not object_exists(aws, key)
    assert not (Path(cfg["paths"]["output"]) / f"{job_id}.json").exists()
    assert inference_calls == {"build_events": [], "without_audio": [], "predict": []}


def test_empty_object_passing_size_check_is_also_rejected(
    aws, make_cfg, roi_masks_small, db, new_job, inference_calls
):
    _, key = new_job()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"")
    outcome = handle_record(
        aws.bucket,
        key,
        s3=aws.s3,
        db=db,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        heartbeat=no_heartbeat,
    )
    assert outcome is Outcome.REJECTED
    assert not object_exists(aws, key)
    assert inference_calls == {"build_events": [], "without_audio": [], "predict": []}


# ---------------------------------------------------------------- process_message


def test_unreadable_upload_end_to_end_deletes_object_and_message(
    aws, make_cfg, roi_masks_small, db, new_job, queue_message, remaining, inference_calls
):
    cfg = make_cfg()
    job_id, key = new_job()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=NOT_A_VIDEO)

    process_message(
        queue_message(s3_event((aws.bucket, key))),
        s3=aws.s3,
        db=db,
        sqs=aws.sqs,
        cfg=cfg,
        roi_masks=roi_masks_small,
        shutdown=worker.ShutdownSignal(),
        max_receives=MAX_RECEIVES,
    )

    assert remaining() == []
    assert not object_exists(aws, key)
    assert not (Path(cfg["paths"]["output"]) / f"{job_id}.json").exists()
    assert inference_calls == {"build_events": [], "without_audio": [], "predict": []}


def test_a_genuine_inference_failure_is_still_not_a_rejection(
    aws,
    make_cfg,
    roi_masks_small,
    db,
    new_job,
    clip_path,
    queue_message,
    remaining,
    patch_everywhere,
):
    """Only unreadable uploads are rejected; a real failure raises and keeps the message."""

    def boom(*a, **kw):
        raise RuntimeError("model crashed")

    patch_everywhere("predict", boom, *MODULES)
    _, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)

    with pytest.raises(RuntimeError, match="model crashed"):
        handle_record(
            aws.bucket,
            key,
            s3=aws.s3,
            db=db,
            cfg=make_cfg(),
            roi_masks=roi_masks_small,
            heartbeat=no_heartbeat,
        )
    assert object_exists(aws, key)

    process_message(
        queue_message(s3_event((aws.bucket, key))),
        s3=aws.s3,
        db=db,
        sqs=aws.sqs,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        shutdown=worker.ShutdownSignal(),
        max_receives=MAX_RECEIVES,
    )
    assert len(remaining()) == 1
    assert object_exists(aws, key)
