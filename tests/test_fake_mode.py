"""Tests for FAKE_INFERENCE mode in neurolens.inference. Written from docs/M1_spec.md 1 and 1a
and docs/M2a_spec.md 7a."""

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from neurolens import inference

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- fake_mode()


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_fake_mode_on_values(monkeypatch, value):
    monkeypatch.setenv("FAKE_INFERENCE", value)
    assert inference.fake_mode() is True


@pytest.mark.parametrize("value", ["0", "false", ""])
def test_fake_mode_off_values(monkeypatch, value):
    monkeypatch.setenv("FAKE_INFERENCE", value)
    assert inference.fake_mode() is False


def test_fake_mode_off_when_unset(monkeypatch):
    monkeypatch.delenv("FAKE_INFERENCE", raising=False)
    assert inference.fake_mode() is False


def test_fake_mode_follows_the_environment_at_call_time(monkeypatch):
    monkeypatch.delenv("FAKE_INFERENCE", raising=False)
    assert inference.fake_mode() is False
    monkeypatch.setenv("FAKE_INFERENCE", "1")
    assert inference.fake_mode() is True
    monkeypatch.delenv("FAKE_INFERENCE")
    assert inference.fake_mode() is False
    monkeypatch.setenv("FAKE_INFERENCE", "yes")
    assert inference.fake_mode() is True


# ---------------------------------------------------------------- probe_duration


def test_probe_duration_measures_the_clip(clip_path):
    assert inference.probe_duration(clip_path) == pytest.approx(3.0, abs=0.3)
    assert inference.probe_duration(str(clip_path)) == pytest.approx(3.0, abs=0.3)


def test_probe_duration_returns_a_float(clip_path):
    assert isinstance(inference.probe_duration(clip_path), float)


# ---------------------------------------------------------------- fake build_events / predict
# M2a section 7a: run_inference and strip_audio are replaced by build_events, without_audio and
# predict. In fake mode predict gives seeded random numbers of shape (ceil(duration), 20484),
# seeded from the file's size (plus 1 for the no-audio pass).


@pytest.fixture
def fake_on(monkeypatch):
    monkeypatch.setenv("FAKE_INFERENCE", "1")


def fake_passes(path, duration=None):
    """(with-audio, no-audio) predictions for a file, as the worker makes them."""
    if duration is None:
        duration = inference.probe_duration(path)
    events = inference.build_events(path)
    full = inference.predict(events, duration)
    noaudio = inference.predict(inference.without_audio(events), duration)
    return full, noaudio


def test_fake_predict_shape(fake_on, clip_path):
    duration = inference.probe_duration(clip_path)
    full, noaudio = fake_passes(clip_path, duration)
    for preds in (full, noaudio):
        assert isinstance(preds, np.ndarray)
        assert preds.shape == (math.ceil(duration), 20484)
        assert np.isfinite(preds).all()


@pytest.mark.parametrize("duration,rows", [(52.2, 53), (119.01, 120), (5.0, 5)])
def test_fake_predict_has_ceil_duration_rows(fake_on, clip_path, duration, rows):
    events = inference.build_events(clip_path)
    assert inference.predict(events, duration).shape == (rows, 20484)


def test_fake_predict_is_repeatable_for_the_same_file(fake_on, clip_path):
    first_full, first_noaudio = fake_passes(clip_path)
    second_full, second_noaudio = fake_passes(clip_path)
    np.testing.assert_array_equal(first_full, second_full)
    np.testing.assert_array_equal(first_noaudio, second_noaudio)


def test_fake_no_audio_pass_differs_from_the_with_audio_pass(fake_on, clip_path):
    full, noaudio = fake_passes(clip_path)
    assert full.shape == noaudio.shape
    assert not np.array_equal(full, noaudio)


def test_fake_predict_is_seeded_from_file_size_not_path(fake_on, clip_path, tmp_path):
    copy = tmp_path / "another_name.mp4"
    copy.write_bytes(clip_path.read_bytes())
    for a, b in zip(fake_passes(clip_path), fake_passes(copy), strict=True):
        np.testing.assert_array_equal(a, b)


def test_fake_predict_differs_between_different_videos(fake_on, clip_path, clip2_path):
    assert clip_path.stat().st_size != clip2_path.stat().st_size
    a, _ = fake_passes(clip_path, 3.0)
    b, _ = fake_passes(clip2_path, 3.0)
    assert not np.array_equal(a, b)


def test_fake_passes_need_no_load_model(fake_on, clip_path, monkeypatch):
    """No atlas, no model: the module-level model is still empty and nothing tries to use it."""
    monkeypatch.setattr(inference, "model", None, raising=False)
    full, noaudio = fake_passes(clip_path)
    assert full.shape[1] == noaudio.shape[1] == 20484


def test_fake_output_feeds_extract_response(fake_on, clip_path, roi_masks_small):
    from neurolens.response import extract_response

    full, noaudio = fake_passes(clip_path)
    result = extract_response(full, noaudio, roi_masks_small)
    assert result["format"] == 2
    assert result["duration_seconds"] == full.shape[0]
    assert len(result["signals"]["overall"]) == full.shape[0]


# ---------------------------------------------------------------- load_model() in fake mode

LOAD_MODEL_SCRIPT = r"""
import hashlib, io, json, logging, os, sys, tempfile, urllib.request
from pathlib import Path
logging.basicConfig(level=logging.INFO)
import numpy as np
import nibabel.freesurfer as freesurfer

areas = ["FFC", "V4t", "PHA1", "PHA2", "PHA3", "V1", "V2", "V3", "A1", "LBelt", "MBelt",
         "PBelt", "A5", "STSva", "STSvp", "45", "PGi", "TE1a"]
stubs = {}
for side, prefix in (("lh", "L"), ("rh", "R")):
    path = Path(tempfile.mkdtemp()) / f"{side}.annot"
    names = [f"{prefix}_{area}_ROI".encode() for area in areas]
    ctab = np.array([[10 * (i + 1), 0, 0, 0] for i in range(len(names))], dtype=np.int32)
    freesurfer.write_annot(str(path), np.arange(10242) % len(names), ctab=ctab, names=names)
    data = path.read_bytes()
    stubs[f"https://stub/{side}"] = data
    digest = hashlib.sha256(data).hexdigest()
    stubs[side] = (f"{side}.HCPMMP1.annot", f"https://stub/{side}", digest)

calls = []
def fake_urlopen(url, timeout=None):
    calls.append(url)
    return io.BytesIO(stubs[url])
urllib.request.urlopen = fake_urlopen

from neurolens import inference
inference.HCP_MMP_FILES = {"lh": stubs["lh"], "rh": stubs["rh"]}
cfg = json.loads(os.environ["TEST_CFG"])
inference.load_model(cfg)
first_calls = len(calls)
inference.load_model(cfg)  # the second start reuses the saved files
masks = inference.roi_masks()
print(json.dumps({
    "heavy": [m for m in ("torch", "tribev2") if m in sys.modules],
    "download_calls": [first_calls, len(calls)],
    "roi_names": sorted(masks),
    "mask_lengths": sorted({len(m) for m in masks.values()}),
    "mask_nonempty": all(bool(m.any()) for m in masks.values()),
    "hf_home": os.environ.get("HF_HOME"),
    "nilearn_data": os.environ.get("NILEARN_DATA"),
}))
"""


def test_load_model_in_fake_mode_skips_torch_but_loads_atlas(tmp_path):
    """Runs in a subprocess so sys.modules and the environment start clean.

    The map download is replaced by a stub serving small synthetic annot files (pinned by their
    own SHA-256), so this needs no network; the masks are built by the real code.
    """
    cfg = {
        "hf_token": "hf_not_a_real_token",
        "paths": {
            "models": str(tmp_path / "models"),
            "data": str(tmp_path / "data"),
        },
        "model": {"repo_id": "facebook/tribev2"},
        "hf_download_timeout": 300,
    }
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HF_", "NILEARN", "TORCH"))}
    env.update(
        FAKE_INFERENCE="1",
        TEST_CFG=json.dumps(cfg),
        NEUROLENS_ROOT=str(tmp_path),
    )
    proc = subprocess.run(
        [sys.executable, "-c", LOAD_MODEL_SCRIPT],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["heavy"] == []  # neither torch nor tribev2 was imported
    assert out["download_calls"] == [2, 2]  # downloaded once, then reused
    assert out["roi_names"] == [
        "bodies",
        "faces",
        "scenes",
        "social",
        "sound",
        "speech",
        "visual",
    ]
    assert out["mask_lengths"] == [20484]
    assert out["mask_nonempty"] is True
    assert out["hf_home"] == str(tmp_path / "models")
    assert out["nilearn_data"] == str(tmp_path / "data" / "nilearn")
    assert "FAKE_INFERENCE is ON" in proc.stderr  # the clear startup warning
