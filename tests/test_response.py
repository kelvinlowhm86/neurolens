"""Tests for neurolens.response, the M3d §2-§3 maths (pure numpy).

Masks: exact Destrieux labels (near misses do not match), missing labels refused, speech on the
left hemisphere only, disjoint masks and none empty. Values: signed means with no rescaling,
groups and overall from the signal series, the muted cut to the shared length, 3-decimal
rounding and the series order. The golden fixture is generated from the real code in step 3:

    python -c "from tests.test_response import write_golden; write_golden()"
"""

import json
from pathlib import Path

import numpy as np
import pytest


def response():
    """Imported lazily so the rest of the suite still collects before the M3d code exists."""
    import neurolens.response as module

    return module


GOLDEN_PATH = Path(__file__).parent / "fixtures" / "extract_response_golden.json"

ALL_LABELS = [
    "G_oc-temp_lat-fusifor",
    "S_oc-temp_lat",
    "G_temporal_inf",
    "G_oc-temp_med-Parahip",
    "S_calcarine",
    "G_cuneus",
    "Pole_occipital",
    "G_oc-temp_med-Lingual",
    "G_temp_sup-G_T_transv",
    "G_temp_sup-Plan_tempo",
    "G_front_inf-Opercular",
    "G_front_inf-Triangul",
    "G_temporal_middle",
    "G_temp_sup-Lateral",
    "S_temporal_sup",
]
SIGNAL_ORDER = ["faces", "bodies", "scenes", "visual", "sound", "speech", "social"]
EXPECTED_SERIES = [*SIGNAL_ORDER, "see", "hear", "understand", "overall"]


# ---------------------------------------------------------------- the signal table


def test_signals_are_the_spec_table():
    assert response().SIGNALS == {
        "faces": ["G_oc-temp_lat-fusifor"],
        "bodies": ["S_oc-temp_lat", "G_temporal_inf"],
        "scenes": ["G_oc-temp_med-Parahip"],
        "visual": ["S_calcarine", "G_cuneus", "Pole_occipital", "G_oc-temp_med-Lingual"],
        "sound": ["G_temp_sup-G_T_transv", "G_temp_sup-Plan_tempo"],
        "speech": [
            "G_front_inf-Opercular",
            "G_front_inf-Triangul",
            "G_temporal_middle",
            "G_temp_sup-Lateral",
        ],
        "social": ["S_temporal_sup"],
    }
    assert list(response().SIGNALS) == SIGNAL_ORDER


def test_left_only_is_speech():
    assert response().LEFT_ONLY == {"speech"}
    assert response().SERIES == EXPECTED_SERIES
    assert response().GROUPS == {
        "see": ["faces", "bodies", "scenes", "visual"],
        "hear": ["sound"],
        "understand": ["speech", "social"],
    }


# ---------------------------------------------------------------- masks


def test_exact_names_match_and_near_misses_do_not():
    near_misses = [f"prefix_{label}" for label in ALL_LABELS]
    near_misses += [f"{label}_suffix" for label in ALL_LABELS]
    names = ["Unknown", *ALL_LABELS, *near_misses]
    labels = np.arange(len(names))

    masks = response().build_masks(labels, names)

    for signal, targets in response().SIGNALS.items():
        target_ids = [names.index(target) for target in targets]
        np.testing.assert_array_equal(masks[signal], np.isin(labels, target_ids))
        assert not masks[signal][names.index(f"prefix_{targets[0]}")]
        assert not masks[signal][names.index(f"{targets[0]}_suffix")]


@pytest.mark.parametrize("missing", ["S_temporal_sup", "G_cuneus"])
def test_a_missing_target_label_raises_and_names_it(missing):
    names = ["Unknown", *(label for label in ALL_LABELS if label != missing)]
    labels = np.arange(len(names))

    with pytest.raises(ValueError) as err:
        response().build_masks(labels, names)

    assert missing in str(err.value)


def test_bytes_label_names_are_decoded_before_matching():
    names = [name.encode() for name in ["Unknown", *ALL_LABELS]]
    labels = np.arange(len(names))

    masks = response().build_masks(labels, names)

    for signal, targets in response().SIGNALS.items():
        target_ids = [names.index(target.encode()) for target in targets]
        np.testing.assert_array_equal(masks[signal], np.isin(labels, target_ids))


def test_speech_is_left_hemisphere_only():
    names = ["Unknown", *ALL_LABELS]
    speech_id = names.index("G_front_inf-Opercular")
    labels = np.full(20484, speech_id, dtype=int)

    masks = response().build_masks(labels, names)

    assert masks["speech"][:10242].all()
    assert not masks["speech"][10242:].any()


def test_masks_are_disjoint_and_none_empty():
    labels = np.arange(20484) % len(ALL_LABELS)
    masks = response().build_masks(labels, ALL_LABELS)

    assert all(mask.any() for mask in masks.values())
    names = list(masks)
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            assert not (masks[left] & masks[right]).any(), (left, right)


def test_require_nonempty_masks_names_every_empty_signal():
    masks = {
        "faces": np.array([True, False]),
        "bodies": np.array([False, False]),
        "scenes": np.array([True, True]),
        "sound": np.array([False, False]),
    }
    with pytest.raises(ValueError) as err:
        response().require_nonempty_masks(masks)
    assert "bodies" in str(err.value)
    assert "sound" in str(err.value)
    assert "faces" not in str(err.value)
    assert "scenes" not in str(err.value)


# ---------------------------------------------------------------- values


def tiny_masks():
    """Each signal covers 10 vertices: facial 0-9, bodies 10-19, ..., social 60-69."""
    masks = {}
    for i, name in enumerate(SIGNAL_ORDER):
        mask = np.zeros(70, dtype=bool)
        mask[i * 10 : (i + 1) * 10] = True
        masks[name] = mask
    return masks


def filled(masks, values, rows):
    preds = np.zeros((rows, 70))
    for name, value in values.items():
        preds[:, masks[name]] = value
    return preds


def test_values_are_signed_means_with_no_abs_and_no_rescaling():
    masks = tiny_masks()
    full = np.zeros((3, 70))
    full[:, masks["faces"]] = np.array([-1.5, -2.25, 0.125])[:, None]
    noaudio = full.copy()

    result = response().extract_response(full, noaudio, masks)

    assert result["signals"]["faces"] == [-1.5, -2.25, 0.125]
    assert min(result["signals"]["faces"]) < 0
    assert result["signals"]["bodies"] == [0.0, 0.0, 0.0]


def test_values_are_rounded_to_three_decimals():
    masks = tiny_masks()
    masks["faces"] = np.zeros(70, dtype=bool)
    masks["faces"][:3] = True
    full = np.zeros((1, 70))
    full[0, masks["faces"]] = [1.0, 0.0, 0.0]
    noaudio = full.copy()

    result = response().extract_response(full, noaudio, masks)

    assert result["signals"]["faces"] == [0.333]
    for series in (result["signals"], result["muted"]):
        for values in series.values():
            assert all(value == round(value, 3) for value in values)


def test_groups_and_overall_are_means_of_the_signal_series():
    masks = tiny_masks()
    full = filled(
        masks,
        {
            "faces": 4.0,
            "bodies": 2.0,
            "scenes": 0.0,
            "visual": 2.0,
            "sound": 3.0,
            "speech": 1.0,
            "social": 3.0,
        },
        rows=2,
    )
    noaudio = full.copy()

    result = response().extract_response(full, noaudio, masks)

    assert result["signals"]["see"] == [2.0, 2.0]
    assert result["signals"]["hear"] == [3.0, 3.0]
    assert result["signals"]["understand"] == [2.0, 2.0]
    assert result["signals"]["overall"] == [round(7 / 3, 3), round(7 / 3, 3)]


def test_a_large_region_does_not_outweigh_a_small_one():
    masks = tiny_masks()
    masks["faces"] = np.zeros(70, dtype=bool)
    masks["faces"][0] = True
    full = filled(masks, {"faces": 10.0, "bodies": 0.0}, rows=1)
    noaudio = full.copy()

    result = response().extract_response(full, noaudio, masks)

    # Mean of the 4 see signals, not of their 1 + 10 + 10 + 10 vertices.
    assert result["signals"]["see"] == [2.5]


def test_muted_is_cut_to_the_shared_length():
    masks = tiny_masks()
    full = np.zeros((5, 70))
    full[:, masks["faces"]] = np.array([1.0, 2.0, 3.0, 4.0, 5.0])[:, None]
    noaudio = np.zeros((3, 70))
    noaudio[:, masks["faces"]] = np.array([10.0, 20.0, 30.0])[:, None]

    result = response().extract_response(full, noaudio, masks)

    assert result["duration_seconds"] == 5
    assert result["signals"]["faces"] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert result["muted"]["faces"] == [10.0, 20.0, 30.0]


def test_muted_is_cut_when_the_noaudio_pass_is_longer():
    masks = tiny_masks()
    full = np.zeros((2, 70))
    full[:, masks["faces"]] = np.array([1.0, 2.0])[:, None]
    noaudio = np.zeros((4, 70))
    noaudio[:, masks["faces"]] = np.array([3.0, 4.0, 5.0, 6.0])[:, None]

    result = response().extract_response(full, noaudio, masks)

    assert result["duration_seconds"] == 2
    assert result["signals"]["faces"] == [1.0, 2.0]
    assert result["muted"]["faces"] == [3.0, 4.0]


def test_output_shape_series_order_and_format():
    masks = tiny_masks()
    full = np.zeros((4, 70))
    noaudio = np.zeros((4, 70))

    result = response().extract_response(full, noaudio, masks)

    assert set(result) == {"format", "duration_seconds", "signals", "muted"}
    assert result["format"] == 2
    assert result["duration_seconds"] == 4
    assert isinstance(result["duration_seconds"], int)
    assert list(result["signals"]) == response().SERIES
    assert list(result["muted"]) == response().SERIES
    for values in result["signals"].values():
        assert len(values) == 4
    for values in result["muted"].values():
        assert len(values) == 4


# ---------------------------------------------------------------- golden


GOLDEN_RECIPE = {"n_vertices": 40, "seed": 7, "full_rows": 6, "noaudio_rows": 5}


def golden_inputs():
    """The fixture's recipe: a tiny synthetic atlas and a seeded random prediction pair."""
    names = ["Unknown", *ALL_LABELS]
    labels = np.arange(GOLDEN_RECIPE["n_vertices"]) % len(names)
    rng = np.random.default_rng(GOLDEN_RECIPE["seed"])
    full = rng.standard_normal((GOLDEN_RECIPE["full_rows"], GOLDEN_RECIPE["n_vertices"]))
    noaudio = rng.standard_normal((GOLDEN_RECIPE["noaudio_rows"], GOLDEN_RECIPE["n_vertices"]))
    return full, noaudio, response().build_masks(labels, names)


def golden_value():
    return response().extract_response(*golden_inputs())


def write_golden(path=GOLDEN_PATH):
    """Step 3 runs this once the real extract_response exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"recipe": GOLDEN_RECIPE, "result": golden_value()}
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def test_golden_extract_response():
    if not GOLDEN_PATH.exists():
        pytest.fail(
            f"{GOLDEN_PATH} is missing. Regenerate it from the new code with: "
            'python -c "from tests.test_response import write_golden; write_golden()"'
        )
    golden = json.loads(GOLDEN_PATH.read_text())
    assert golden["recipe"] == GOLDEN_RECIPE, "the golden recipe changed; regenerate the fixture"
    assert golden_value() == golden["result"]


if __name__ == "__main__":
    print(write_golden())
