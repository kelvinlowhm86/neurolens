"""Gaze trial's pure geometry and share functions (experiments/gaze_trial.py)."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS = REPO_ROOT / "experiments"


def load(name):
    spec = importlib.util.spec_from_file_location(f"experiment_{name}", EXPERIMENTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_centre_square_for_a_1920x1080_frame():
    gaze = load("gaze_trial")
    side = 1080 * 256 / 292
    assert gaze.centre_square(1920, 1080) == pytest.approx(
        ((1920 - side) / 2, (1080 - side) / 2, (1920 + side) / 2, (1080 + side) / 2)
    )


def test_centre_square_for_a_720x1280_frame():
    gaze = load("gaze_trial")
    side = 720 * 256 / 292
    assert gaze.centre_square(720, 1280) == pytest.approx(
        ((720 - side) / 2, (1280 - side) / 2, (720 + side) / 2, (1280 + side) / 2)
    )


def test_outside_share_is_zero_when_all_mass_is_inside():
    gaze = load("gaze_trial")
    saliency = np.zeros((100, 200))
    saliency[50, 100] = 3.0
    assert gaze.outside_share(saliency, (25.5, 10.25, 175.5, 90.75)) == pytest.approx(0.0)


def test_outside_share_is_one_when_all_mass_is_outside():
    gaze = load("gaze_trial")
    saliency = np.zeros((100, 200))
    saliency[5, 5] = 3.0
    assert gaze.outside_share(saliency, (50, 20, 150, 80)) == pytest.approx(1.0)


def test_outside_share_of_a_uniform_map_is_the_area_outside_the_box():
    gaze = load("gaze_trial")
    width, height = 1920, 1080
    box = gaze.centre_square(width, height)
    side = box[2] - box[0]
    expected = 1 - side * side / (width * height)
    for scale in (1.0, 7.5):
        assert gaze.outside_share(np.full((height, width), scale), box) == pytest.approx(expected)


def test_outside_share_of_a_zero_map_is_zero_not_nan():
    gaze = load("gaze_trial")
    assert gaze.outside_share(np.zeros((10, 10)), (2, 2, 8, 8)) == 0.0


def test_centre_blob_likeness():
    gaze = load("gaze_trial")
    blob = gaze.centre_blob(90, 160)
    corner = np.zeros((90, 160))
    corner[5:15, 5:20] = 1.0
    assert gaze.correlation(blob, blob) == pytest.approx(1.0)
    assert gaze.correlation(corner, blob) < 0
    assert gaze.correlation(np.ones((90, 160)), blob) == 0.0
