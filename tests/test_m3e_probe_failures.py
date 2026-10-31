"""Probe failures that are not the file's fault are retried, not refunded (M3e §3, §11).

`probe_duration` blames the video only when ffprobe read it and found it unusable (no video
stream, no timestamped packets, a non-finite duration, or "Invalid data found when processing
input"). A timeout, a fetch error such as an S3 403, any other non-zero exit and output that is
not JSON raise `ProbeFailed`, which the dispatcher lets propagate so SQS redelivers it.
"""

import subprocess

import pytest
from conftest import STARTER_CENTS
from neurolens import pricing


def media():
    from neurolens import media

    return media


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True, capture_output=True)


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "clip.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=160x120:rate=10:duration=3",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(path),
    )
    return path


def completed(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------- unreadable files


def test_a_text_file_is_unreadable(tmp_path):
    path = tmp_path / "text.mp4"
    path.write_text("this is not a video")
    with pytest.raises(media().UnreadableVideo):
        media().probe_duration(path)


def test_a_truncated_mp4_is_unreadable(video, tmp_path):
    data = video.read_bytes()
    truncated = tmp_path / "truncated.mp4"
    truncated.write_bytes(data[: len(data) // 2])
    with pytest.raises(media().UnreadableVideo):
        media().probe_duration(truncated)


def test_an_audio_only_file_is_unreadable(tmp_path):
    path = tmp_path / "audio.mp4"
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=3", str(path))
    with pytest.raises(media().UnreadableVideo):
        media().probe_duration(path)


def test_invalid_data_from_ffprobe_is_unreadable(monkeypatch):
    stderr = "[mov] moov atom not found\nfile.mp4: Invalid data found when processing input"
    monkeypatch.setattr(media().subprocess, "run", lambda *a, **kw: completed(a, 1, stderr=stderr))
    with pytest.raises(media().UnreadableVideo):
        media().probe_duration("file.mp4")


# ---------------------------------------------------------------- transient failures


def test_ffprobe_runs_at_error_verbosity(video, monkeypatch):
    seen = []
    real = subprocess.run

    def spy(*args, **kwargs):
        seen.append(args[0])
        return real(*args, **kwargs)

    monkeypatch.setattr(media().subprocess, "run", spy)
    media().probe_duration(video)
    assert seen and seen[0][1:3] == ["-v", "error"]


def test_a_timeout_is_probe_failed(monkeypatch):
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffprobe", 30, stderr="connection reset")

    monkeypatch.setattr(media().subprocess, "run", run)
    with pytest.raises(media().ProbeFailed, match="connection reset"):
        media().probe_duration("clip.mp4")


def test_a_fetch_error_is_probe_failed_with_ffprobe_text(monkeypatch):
    stderr = "https://bucket.s3.amazonaws.com/video.mp4: Server returned 403 Forbidden"

    def run(*args, **kwargs):
        return completed(args, 1, stderr=stderr)

    monkeypatch.setattr(media().subprocess, "run", run)
    with pytest.raises(media().ProbeFailed, match="403 Forbidden"):
        media().probe_duration("https://bucket.s3.amazonaws.com/video.mp4")


def test_output_that_is_not_json_is_probe_failed(monkeypatch):
    def run(*args, **kwargs):
        return completed(args, 0, stdout="not json", stderr="")

    monkeypatch.setattr(media().subprocess, "run", run)
    with pytest.raises(media().ProbeFailed):
        media().probe_duration("clip.mp4")


def test_probe_failed_is_not_a_value_error():
    assert issubclass(media().ProbeFailed, RuntimeError)
    assert not issubclass(media().ProbeFailed, ValueError)


# ---------------------------------------------------------------- the dispatcher


class RecordingSageMaker:
    def __init__(self):
        self.calls = []

    def invoke_endpoint_async(self, **kwargs):
        self.calls.append(kwargs)
        return {"InferenceId": kwargs["InferenceId"]}


class NotPausedAppScaling:
    def describe_scalable_targets(self, **kwargs):
        return {"ScalableTargets": [{"MinCapacity": 0, "MaxCapacity": 1}]}


def test_probe_failed_propagates_and_leaves_the_job_queued(aws, db, pg, new_job, monkeypatch):
    from neurolens import storage
    from neurolens.lambdas import dispatcher

    job_id, key = new_job()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"an uploaded video")

    def fail(*args, **kwargs):
        raise media().ProbeFailed("ffprobe failed: Server returned 403 Forbidden")

    monkeypatch.setattr(dispatcher, "probe_duration", fail)
    sagemaker = RecordingSageMaker()

    with pytest.raises(media().ProbeFailed, match="403 Forbidden"):
        dispatcher.dispatch(
            aws.bucket,
            key,
            s3=aws.s3,
            sagemaker=sagemaker,
            appscaling=NotPausedAppScaling(),
            db=db,
            endpoint="neurolens-gpu",
            max_upload_bytes=300_000_000,
            max_duration_s=120,
        )

    job = pg.job(job_id)
    assert job["status"] == "queued" and job["verified_duration_ms"] is None
    assert job["error_code"] is None
    price = pricing.estimate_cost_cents(3)
    assert pg.balance() == (STARTER_CENTS - price, price)
    assert storage.object_exists(aws.s3, aws.bucket, key)
    assert sagemaker.calls == []


def test_a_valid_probe_still_returns_the_measured_seconds(video):
    assert media().probe_duration(video) == pytest.approx(3.0, abs=0.3)
