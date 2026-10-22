"""Guards against a silently wrong result or a wrong price: an empty ROI mask or a NaN must stop
the job loudly (it is then retried and refunded) instead of publishing a result the browser cannot
parse after the user was charged; probe_duration's unreadable and negative-start paths.
"""

import json
import subprocess

import numpy as np
import pytest
from neurolens import storage


def masks(**sizes):
    return {name: np.arange(10) < n for name, n in sizes.items()}


# ---------------------------------------------------------------- empty ROI masks


def test_require_nonempty_masks_accepts_masks_that_all_have_vertices():
    from neurolens.response import require_nonempty_masks

    require_nonempty_masks(masks(faces=3, sound=1))


def test_require_nonempty_masks_names_every_empty_signal():
    from neurolens.response import require_nonempty_masks

    with pytest.raises(ValueError) as err:
        require_nonempty_masks(masks(faces=0, bodies=2, sound=0))
    assert "faces" in str(err.value)
    assert "sound" in str(err.value)
    assert "bodies" not in str(err.value)


# ---------------------------------------------------------------- NaN is never published


def test_put_result_refuses_nan_and_writes_nothing(aws):
    result = {"format": 2, "signals": {"overall": [float("nan")]}}
    with pytest.raises(ValueError):
        storage.put_result(aws.s3, aws.bucket, "j1", result)
    assert not storage.result_exists(aws.s3, aws.bucket, "j1")


def test_put_result_refuses_infinity(aws):
    with pytest.raises(ValueError):
        storage.put_result(aws.s3, aws.bucket, "j1", {"value": float("inf")})
    assert not storage.result_exists(aws.s3, aws.bucket, "j1")


# ---------------------------------------------------------------- probe_duration paths


def fake_ffprobe(monkeypatch, stdout):
    from neurolens import media

    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(media.subprocess, "run", run)


def test_probe_duration_timeout_is_an_unreadable_video(monkeypatch):
    from neurolens import media

    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffprobe", 30)

    monkeypatch.setattr(media.subprocess, "run", run)
    with pytest.raises(media.UnreadableVideo):
        media.probe_duration("clip.mp4")


def test_probe_duration_counts_a_negative_start_as_zero(monkeypatch):
    info = {
        "streams": [{"codec_type": "video"}],
        "packets": [
            {"pts_time": "-0.5", "duration_time": "1.0"},
            {"pts_time": "3.0", "duration_time": "1.0"},
        ],
    }
    fake_ffprobe(monkeypatch, json.dumps(info))
    # earliest start -0.5 counts as 0; latest end is 4.0
    from neurolens import media

    assert media.probe_duration("clip.mp4") == pytest.approx(4.0)


def test_probe_duration_ignores_packets_without_a_timestamp(monkeypatch):
    info = {
        "streams": [{"codec_type": "video"}],
        "packets": [{"duration_time": "9"}, {"pts_time": "1.0", "duration_time": "2.0"}],
    }
    fake_ffprobe(monkeypatch, json.dumps(info))
    from neurolens import media

    assert media.probe_duration("clip.mp4") == pytest.approx(2.0)


def test_probe_duration_with_no_output_is_an_unreadable_video(monkeypatch):
    from neurolens import media

    fake_ffprobe(monkeypatch, "")
    with pytest.raises(media.UnreadableVideo):
        media.probe_duration("clip.mp4")
