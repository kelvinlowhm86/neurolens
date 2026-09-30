"""Tests for FAKE_INFERENCE mode in neurolens.inference. Written from docs/M1_spec.md 1 and 1a."""

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


# ---------------------------------------------------------------- fake run_inference


@pytest.fixture
def fake_on(monkeypatch):
    monkeypatch.setenv("FAKE_INFERENCE", "1")


def test_fake_run_inference_shape(fake_on, clip_path):
    preds = inference.run_inference(clip_path)
    expected_rows = math.ceil(inference.probe_duration(clip_path))
    assert isinstance(preds, np.ndarray)
    assert preds.shape == (expected_rows, 20484)
    assert np.isfinite(preds).all()


def test_fake_run_inference_is_deterministic_for_the_same_file(fake_on, clip_path):
    first = inference.run_inference(clip_path)
    second = inference.run_inference(clip_path)
    np.testing.assert_array_equal(first, second)


def test_fake_run_inference_is_seeded_from_file_size_not_path(fake_on, clip_path, tmp_path):
    copy = tmp_path / "another_name.mp4"
    copy.write_bytes(clip_path.read_bytes())
    np.testing.assert_array_equal(inference.run_inference(clip_path), inference.run_inference(copy))


def test_fake_run_inference_differs_between_different_videos(fake_on, clip_path, clip2_path):
    assert clip_path.stat().st_size != clip2_path.stat().st_size
    a = inference.run_inference(clip_path)
    b = inference.run_inference(clip2_path)
    assert a.shape != b.shape or not np.array_equal(a, b)


def test_fake_run_inference_needs_no_load_model(fake_on, clip_path, monkeypatch):
    """No atlas, no model: the module-level model is still empty and nothing tries to use it."""
    monkeypatch.setattr(inference, "model", None, raising=False)
    assert inference.run_inference(clip_path).shape[1] == 20484


def test_fake_output_feeds_extract_engagement(fake_on, clip_path, roi_masks_small):
    from neurolens.engagement import extract_engagement

    preds = inference.run_inference(clip_path)
    result = extract_engagement(preds, preds, roi_masks_small)
    assert result["duration_seconds"] == preds.shape[0]
    assert len(result["timesteps"]) == preds.shape[0]


# ---------------------------------------------------------------- load_model() in fake mode

LOAD_MODEL_SCRIPT = r"""
import json, logging, os, sys
logging.basicConfig(level=logging.INFO)
import numpy as np
import nilearn.datasets as nd

calls = []
labels = ["Unknown", "G_oc-temp_lat-fusifor", "S_oc-temp_lat", "G_temporal_inf",
          "G_oc-temp_med-Parahip", "S_temporal_sup", "G_temp_sup-G_T_transv",
          "G_temp_sup-Plan_tempo"]

def fake_fetch(*args, **kwargs):
    calls.append(1)
    return {"map_left": np.arange(10242) % 8, "map_right": np.arange(10242) % 8,
            "labels": labels}

nd.fetch_atlas_surf_destrieux = fake_fetch
from neurolens import inference
cfg = json.loads(os.environ["TEST_CFG"])
inference.load_model(cfg)
masks = inference.roi_masks()
print(json.dumps({
    "heavy": [m for m in ("torch", "tribev2") if m in sys.modules],
    "atlas_calls": len(calls),
    "roi_names": sorted(masks),
    "mask_lengths": sorted({len(m) for m in masks.values()}),
    "mask_nonempty": all(bool(m.any()) for m in masks.values()),
    "hf_home": os.environ.get("HF_HOME"),
    "nilearn_data": os.environ.get("NILEARN_DATA"),
}))
"""


def test_load_model_in_fake_mode_skips_torch_but_loads_atlas(tmp_path):
    """Runs in a subprocess so sys.modules and the environment start clean.

    The atlas download is replaced by a stub, so this needs no network; the ROI masks are
    built by the real code from the stub's labels.
    """
    cfg = {
        "hf_token": "hf_not_a_real_token",
        "paths": {
            "models": str(tmp_path / "models"),
            "data": str(tmp_path / "data"),
            "output": str(tmp_path / "output"),
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
    assert out["atlas_calls"] == 1
    assert out["roi_names"] == ["auditory", "eba_bodies", "ffa_faces", "ppa_scenes", "sts_social"]
    assert out["mask_lengths"] == [20484]
    assert out["mask_nonempty"] is True
    assert out["hf_home"] == str(tmp_path / "models")
    assert out["nilearn_data"] == str(tmp_path / "data" / "nilearn")
    assert "FAKE_INFERENCE is ON" in proc.stderr  # the clear startup warning
