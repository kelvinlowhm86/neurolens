"""The unreadable-video rule (docs/M1_spec.md §1, §5, §6a; M3e §3, §4b).

ffprobe cannot measure a file that is not a video, so `media.probe_duration` raises
`UnreadableVideo`. The dispatcher refuses such an upload before any GPU runs (covered by
tests/test_m3e_dispatcher.py); the container also refuses one it is handed, refunding it as
`unreadable_video` and deleting its files. Audio-only files count as unreadable too.
"""

import contextlib
import os
import subprocess

import pytest
from conftest import STARTER_CENTS
from neurolens import billing, settings, worker
from neurolens.worker import Outcome

MODULES = ("neurolens.inference", "neurolens.worker")
NOT_A_VIDEO = b"this is not a video"


def media_module():
    from neurolens import media

    return media


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


def verify(db, job_id):
    assert billing.verify_queued(db, job_id, 3000, settings.max_duration(None)) == "ok"


def run_job(aws, db, job_id, video_path, cfg, masks):
    return worker.handle_job(
        job_id,
        str(video_path),
        s3=aws.s3,
        db=db,
        cfg=cfg,
        roi_masks=masks,
        keep_alive=lambda on_beat: contextlib.nullcontext(),
    )


def object_exists(aws, key):
    from neurolens import storage

    return storage.object_exists(aws.s3, aws.bucket, key)


# ---------------------------------------------------------------- probe_duration


def test_unreadable_video_is_a_value_error_subclass():
    assert issubclass(media_module().UnreadableVideo, ValueError)


@pytest.mark.parametrize(
    "content",
    [NOT_A_VIDEO, b"", os.urandom(4096)],
    ids=["plain_text", "empty_file", "random_bytes"],
)
def test_probe_duration_raises_unreadable_video(tmp_path, content):
    path = tmp_path / "bad.mp4"
    path.write_bytes(content)
    media = media_module()
    with pytest.raises(media.UnreadableVideo):
        media.probe_duration(path)


def test_probe_duration_still_measures_a_real_clip(clip_path):
    duration = media_module().probe_duration(clip_path)
    assert isinstance(duration, float)
    assert duration == pytest.approx(3.0, abs=0.5)


def test_probe_duration_raises_for_an_audio_only_file(audio_only_path):
    media = media_module()
    with pytest.raises(media.UnreadableVideo):
        media.probe_duration(audio_only_path)


def test_probe_duration_runs_ffprobe_with_a_30_second_default_timeout(clip_path, monkeypatch):
    seen = []
    real = subprocess.run

    def spy(*args, **kwargs):
        seen.append(kwargs.get("timeout"))
        return real(*args, **kwargs)

    media = media_module()
    monkeypatch.setattr(media.subprocess, "run", spy)
    media.probe_duration(clip_path)
    assert seen and all(timeout == 30 for timeout in seen)


def test_probe_duration_takes_the_dispatchers_longer_timeout(clip_path, monkeypatch):
    seen = []
    real = subprocess.run

    def spy(*args, **kwargs):
        seen.append(kwargs.get("timeout"))
        return real(*args, **kwargs)

    media = media_module()
    monkeypatch.setattr(media.subprocess, "run", spy)
    media.probe_duration(clip_path, timeout_s=90)
    assert seen and all(timeout == 90 for timeout in seen)


# ---------------------------------------------------------------- handle_job


def test_unreadable_upload_is_rejected_refunded_and_deleted(
    aws, db, pg, make_cfg, roi_masks_small, new_job, inference_calls, tmp_path
):
    job_id, key = new_job()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=NOT_A_VIDEO)
    verify(db, job_id)
    # SageMaker streams the same bytes to disk; hand handle_job a local copy.
    garbage = tmp_path / "bad.mp4"
    garbage.write_bytes(NOT_A_VIDEO)

    outcome = run_job(aws, db, job_id, garbage, make_cfg(), roi_masks_small)

    assert outcome is Outcome.REJECTED
    assert not object_exists(aws, key)
    assert pg.refund_reason(job_id) == "unreadable_video"
    assert pg.balance() == (STARTER_CENTS, 0)
    assert inference_calls == {"build_events": [], "without_audio": [], "predict": []}


def test_empty_file_is_also_rejected(
    aws, db, pg, make_cfg, roi_masks_small, new_job, inference_calls, tmp_path
):
    job_id, key = new_job()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"")
    verify(db, job_id)
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")

    outcome = run_job(aws, db, job_id, empty, make_cfg(), roi_masks_small)

    assert outcome is Outcome.REJECTED
    assert not object_exists(aws, key)
    assert pg.refund_reason(job_id) == "unreadable_video"
    assert inference_calls == {"build_events": [], "without_audio": [], "predict": []}


def test_audio_only_upload_is_rejected_and_deleted_without_inference(
    aws, db, pg, make_cfg, roi_masks_small, new_job, audio_only_path, inference_calls
):
    job_id, key = new_job()
    aws.s3.upload_file(str(audio_only_path), aws.bucket, key)
    verify(db, job_id)

    outcome = run_job(aws, db, job_id, audio_only_path, make_cfg(), roi_masks_small)

    assert outcome is Outcome.REJECTED
    assert not object_exists(aws, key)
    assert pg.job(job_id)["error_code"] == "unreadable_video"
    assert inference_calls == {"build_events": [], "without_audio": [], "predict": []}


def test_a_genuine_inference_failure_is_failed_not_a_rejection(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    """Only unreadable uploads are rejections; a real failure refunds as processing_failed."""

    def boom(*a, **kw):
        raise RuntimeError("model crashed")

    patch_everywhere("predict", boom, *MODULES)
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)

    outcome = run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small)

    assert outcome is Outcome.FAILED
    assert pg.job(job_id)["error_code"] == "processing_failed"
    assert not object_exists(aws, key)
