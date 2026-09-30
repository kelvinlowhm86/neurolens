"""The unreadable-video rule. Written from docs/M1_spec.md sections 1, 5 and 6a.

ffprobe cannot measure a file that is not a video, so probe_duration raises UnreadableVideo
and the worker rejects the upload (deletes it and its message) instead of retrying forever.
"""

import json
import os
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from neurolens import inference
from neurolens.worker import Outcome, handle_record, process_message

MODULES = ("neurolens.inference", "neurolens.worker")
NOT_A_VIDEO = b"this is not a video"


@pytest.fixture(autouse=True)
def fake_on(monkeypatch):
    monkeypatch.setenv("FAKE_INFERENCE", "1")


@pytest.fixture(autouse=True)
def no_gpu(patch_everywhere):
    patch_everywhere("gpu_info", lambda: None, *MODULES)


@pytest.fixture
def inference_calls(patch_everywhere):
    """Records any call to run_inference or strip_audio (neither may happen)."""
    log = {"run_inference": [], "strip_audio": []}

    def run(path, *a, **kw):
        log["run_inference"].append(str(path))
        raise AssertionError("run_inference must not be called for an unreadable video")

    def strip(src, dst, *a, **kw):
        log["strip_audio"].append(str(src))
        raise AssertionError("strip_audio must not be called for an unreadable video")

    patch_everywhere("run_inference", run, *MODULES)
    patch_everywhere("strip_audio", strip, *MODULES)
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
    aws, make_cfg, roi_masks_small, new_key, inference_calls
):
    cfg = make_cfg()
    job_id, key = new_key()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=NOT_A_VIDEO)

    outcome = handle_record(aws.bucket, key, s3=aws.s3, cfg=cfg, roi_masks=roi_masks_small)

    assert outcome is Outcome.REJECTED
    assert not object_exists(aws, key)
    assert not (Path(cfg["paths"]["output"]) / f"{job_id}.json").exists()
    assert inference_calls["run_inference"] == []
    assert inference_calls["strip_audio"] == []


def test_empty_object_passing_size_check_is_also_rejected(
    aws, make_cfg, roi_masks_small, new_key, inference_calls
):
    _, key = new_key()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"")
    outcome = handle_record(aws.bucket, key, s3=aws.s3, cfg=make_cfg(), roi_masks=roi_masks_small)
    assert outcome is Outcome.REJECTED
    assert not object_exists(aws, key)
    assert inference_calls["run_inference"] == []


# ---------------------------------------------------------------- process_message


def test_unreadable_upload_end_to_end_deletes_object_and_message(
    aws, make_cfg, roi_masks_small, new_key, queue_message, remaining, inference_calls
):
    cfg = make_cfg()
    job_id, key = new_key()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=NOT_A_VIDEO)

    process_message(
        queue_message(s3_event((aws.bucket, key))),
        s3=aws.s3,
        sqs=aws.sqs,
        cfg=cfg,
        roi_masks=roi_masks_small,
    )

    assert remaining() == []
    assert not object_exists(aws, key)
    assert not (Path(cfg["paths"]["output"]) / f"{job_id}.json").exists()
    assert inference_calls["run_inference"] == []
    assert inference_calls["strip_audio"] == []


def test_a_genuine_inference_failure_is_still_not_a_rejection(
    aws, make_cfg, roi_masks_small, new_key, clip_path, queue_message, remaining, patch_everywhere
):
    """Only unreadable uploads are rejected; a real failure raises and keeps the message."""

    def boom(path):
        raise RuntimeError("model crashed")

    patch_everywhere("run_inference", boom, *MODULES)
    _, key = new_key()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)

    with pytest.raises(RuntimeError, match="model crashed"):
        handle_record(aws.bucket, key, s3=aws.s3, cfg=make_cfg(), roi_masks=roi_masks_small)
    assert object_exists(aws, key)

    process_message(
        queue_message(s3_event((aws.bucket, key))),
        s3=aws.s3,
        sqs=aws.sqs,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
    )
    assert len(remaining()) == 1
    assert object_exists(aws, key)
