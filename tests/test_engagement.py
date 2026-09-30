"""Tests for neurolens.engagement (pure maths, numpy only). Written from docs/M0_spec.md."""

import json
from pathlib import Path

import numpy as np
from neurolens.engagement import (
    ROI_LABEL_MAP,
    build_roi_masks,
    extract_engagement,
    normalize_01,
)

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "extract_engagement_golden.json"
ROI_ORDER = ["ffa_faces", "eba_bodies", "ppa_scenes", "sts_social", "auditory"]
N_VERTICES = 20484
REGION_KEYS = set(ROI_ORDER)
STEP_KEYS = {
    "t",
    "engagement_overall",
    "regions",
    "auditory_with_audio",
    "auditory_without_audio",
}


def golden_masks():
    """ROI i is True on vertices [i*200, i*200 + 200), False elsewhere (spec section 5.1)."""
    masks = {}
    for i, name in enumerate(ROI_ORDER):
        mask = np.zeros(N_VERTICES, dtype=bool)
        mask[i * 200 : i * 200 + 200] = True
        masks[name] = mask
    return masks


def golden_inputs(case):
    """Rebuild the inputs from the recipe stored in the fixture and in the spec."""
    # "short_edge" uses seed 1: its dropped last row is the min or max of the auditory series,
    # so cut-then-rescale and rescale-then-cut differ (seed 0 cannot tell them apart).
    rng = np.random.default_rng(1 if case == "short_edge" else 0)
    full = rng.standard_normal((12, N_VERTICES))
    if case == "equal":
        noaudio = rng.standard_normal((12, N_VERTICES))
    elif case in ("short", "short_edge"):
        noaudio = rng.standard_normal((11, N_VERTICES))
    else:
        raise ValueError(case)
    return full, noaudio


def small_masks(n_vertices=50):
    """Tiny masks: ROI i covers 5 vertices starting at i*10."""
    masks = {}
    for i, name in enumerate(ROI_ORDER):
        mask = np.zeros(n_vertices, dtype=bool)
        mask[i * 10 : i * 10 + 5] = True
        masks[name] = mask
    return masks


def small_inputs(t_full, t_noaudio, n_vertices=50, seed=1):
    rng = np.random.default_rng(seed)
    return (
        rng.standard_normal((t_full, n_vertices)),
        rng.standard_normal((t_noaudio, n_vertices)),
    )


# ---- golden comparison ------------------------------------------------------


def test_golden_recipe_matches_spec():
    golden = json.loads(GOLDEN_PATH.read_text())
    assert golden["recipe"]["roi_order"] == ROI_ORDER
    assert golden["recipe"]["n_vertices"] == N_VERTICES
    assert set(golden["cases"]) == {"equal", "short", "short_edge"}


def test_roi_label_map_keys_and_order():
    assert list(ROI_LABEL_MAP) == ROI_ORDER
    assert ROI_LABEL_MAP == {
        "ffa_faces": ["G_oc-temp_lat-fusifor"],
        "eba_bodies": ["S_oc-temp_lat", "G_temporal_inf"],
        "ppa_scenes": ["G_oc-temp_med-Parahip"],
        "sts_social": ["S_temporal_sup"],
        "auditory": ["G_temp_sup-G_T_transv", "G_temp_sup-Plan_tempo"],
    }


def test_golden_equal_length():
    golden = json.loads(GOLDEN_PATH.read_text())["cases"]["equal"]
    full, noaudio = golden_inputs("equal")
    result = extract_engagement(full, noaudio, golden_masks())
    assert result == golden


def test_golden_shorter_noaudio():
    golden = json.loads(GOLDEN_PATH.read_text())["cases"]["short"]
    full, noaudio = golden_inputs("short")
    result = extract_engagement(full, noaudio, golden_masks())
    assert result == golden


def test_golden_shorter_noaudio_edge():
    golden = json.loads(GOLDEN_PATH.read_text())["cases"]["short_edge"]
    full, noaudio = golden_inputs("short_edge")
    result = extract_engagement(full, noaudio, golden_masks())
    assert result == golden


# ---- behaviour checks -------------------------------------------------------


def test_normalize_01_scales_to_unit_range():
    out = normalize_01(np.array([2.0, 4.0, 6.0]))
    assert np.allclose(out, [0.0, 0.5, 1.0])


def test_normalize_01_constant_gives_zeros():
    out = normalize_01(np.full(5, 3.7))
    assert out.shape == (5,)
    assert np.all(out == 0.0)


def test_constant_series_gives_zeros():
    full = np.ones((6, 50))
    noaudio = np.ones((6, 50))
    result = extract_engagement(full, noaudio, small_masks())
    assert len(result["timesteps"]) == 6
    for step in result["timesteps"]:
        assert step["engagement_overall"] == 0.0
        assert all(v == 0.0 for v in step["regions"].values())
        assert step["auditory_with_audio"] == 0.0
        assert step["auditory_without_audio"] == 0.0


def test_output_length_and_keys():
    full, noaudio = small_inputs(9, 9)
    result = extract_engagement(full, noaudio, small_masks())
    assert set(result) == {"duration_seconds", "timesteps"}
    assert result["duration_seconds"] == 9
    assert isinstance(result["duration_seconds"], int)
    assert len(result["timesteps"]) == 9
    for t, step in enumerate(result["timesteps"]):
        assert set(step) == STEP_KEYS
        assert step["t"] == t
        assert set(step["regions"]) == REGION_KEYS


def test_values_are_rounded_to_four_places_and_in_unit_range():
    full, noaudio = small_inputs(9, 9)
    result = extract_engagement(full, noaudio, small_masks())
    for step in result["timesteps"]:
        values = [
            step["engagement_overall"],
            *step["regions"].values(),
            step["auditory_with_audio"],
            step["auditory_without_audio"],
        ]
        for v in values:
            assert 0.0 <= v <= 1.0
            assert v == round(v, 4)


def test_none_rows_where_noaudio_pass_is_shorter():
    full, noaudio = small_inputs(10, 7)
    result = extract_engagement(full, noaudio, small_masks())
    steps = result["timesteps"]
    assert result["duration_seconds"] == 10
    assert len(steps) == 10
    for t, step in enumerate(steps):
        if t < 7:
            assert step["auditory_with_audio"] is not None
            assert step["auditory_without_audio"] is not None
        else:
            assert step["auditory_with_audio"] is None
            assert step["auditory_without_audio"] is None
        # the five main ROI columns and the overall score are never None
        assert step["engagement_overall"] is not None
        assert all(v is not None for v in step["regions"].values())


def test_no_none_rows_when_passes_have_equal_length():
    full, noaudio = small_inputs(8, 8)
    steps = extract_engagement(full, noaudio, small_masks())["timesteps"]
    assert all(s["auditory_with_audio"] is not None for s in steps)
    assert all(s["auditory_without_audio"] is not None for s in steps)


def test_no_none_rows_when_noaudio_pass_is_longer():
    # The shared length is min(len(full), len(noaudio)) = 6, and there is one row per
    # row of preds_full, so nothing is cut short.
    full, noaudio = small_inputs(6, 9)
    result = extract_engagement(full, noaudio, small_masks())
    assert result["duration_seconds"] == 6
    steps = result["timesteps"]
    assert len(steps) == 6
    assert all(s["auditory_with_audio"] is not None for s in steps)
    assert all(s["auditory_without_audio"] is not None for s in steps)


# ---- build_roi_masks --------------------------------------------------------

FAKE_NAMES = [
    "Unknown",  # 0
    "G_oc-temp_lat-fusifor",  # 1
    "S_oc-temp_lat",  # 2
    "G_temporal_inf",  # 3
    "G_oc-temp_med-Parahip",  # 4
    "S_temporal_sup",  # 5
    "G_temp_sup-G_T_transv",  # 6
    "G_temp_sup-Plan_tempo",  # 7
    "S_other",  # 8
]
# 18 vertices: each label id 0..8 appears twice.
FAKE_LABELS = np.array([0, 1, 2, 3, 4, 5, 6, 7, 8, 8, 7, 6, 5, 4, 3, 2, 1, 0])


def test_build_roi_masks_tiny_atlas():
    masks = build_roi_masks(FAKE_LABELS, FAKE_NAMES)
    assert list(masks) == ROI_ORDER

    def expected(*label_ids):
        return np.isin(FAKE_LABELS, label_ids)

    assert np.array_equal(masks["ffa_faces"], expected(1))
    assert np.array_equal(masks["eba_bodies"], expected(2, 3))
    assert np.array_equal(masks["ppa_scenes"], expected(4))
    assert np.array_equal(masks["sts_social"], expected(5))
    assert np.array_equal(masks["auditory"], expected(6, 7))
    for mask in masks.values():
        assert mask.dtype == bool
        assert mask.shape == FAKE_LABELS.shape


def test_build_roi_masks_matches_by_substring():
    names = list(FAKE_NAMES)
    names[5] = "prefix_S_temporal_sup_suffix"
    masks = build_roi_masks(FAKE_LABELS, names)
    assert np.array_equal(masks["sts_social"], np.isin(FAKE_LABELS, [5]))


def test_build_roi_masks_no_matching_label_gives_all_false():
    masks = build_roi_masks(FAKE_LABELS, ["Unknown"] * 9)
    assert list(masks) == ROI_ORDER
    for mask in masks.values():
        assert mask.dtype == bool
        assert not mask.any()


def test_build_roi_masks_custom_label_map():
    masks = build_roi_masks(FAKE_LABELS, FAKE_NAMES, {"mine": ["S_other"]})
    assert list(masks) == ["mine"]
    assert np.array_equal(masks["mine"], np.isin(FAKE_LABELS, [8]))
