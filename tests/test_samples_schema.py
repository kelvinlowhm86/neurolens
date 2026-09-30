"""data/samples.json keeps the keys static/index.html reads. From docs/M0_spec.md.

Deliberately loose: only keys the page depends on, not types or optional fields.
"""

import json
from pathlib import Path

import pytest
from neurolens.engagement import ROI_LABEL_MAP

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SAMPLES = json.loads((DATA_DIR / "samples.json").read_text())["samples"]


def test_there_are_samples():
    assert len(SAMPLES) > 0


@pytest.mark.parametrize("sample", SAMPLES, ids=[s.get("id", "?") for s in SAMPLES])
def test_sample_has_frontend_keys(sample):
    for key in ("title", "video_file", "duration_seconds"):
        assert key in sample
    assert sample["timesteps"]


@pytest.mark.parametrize("sample", SAMPLES, ids=[s.get("id", "?") for s in SAMPLES])
def test_sample_timesteps_have_frontend_keys(sample):
    for step in sample["timesteps"]:
        assert "engagement_overall" in step
        assert set(ROI_LABEL_MAP) <= set(step["regions"])
        for key in ("auditory_with_audio", "auditory_without_audio"):
            assert key in step
