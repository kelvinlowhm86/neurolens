"""Tests for neurolens.response, the M3d §2-§3 maths (pure numpy).

Values: signed means with no rescaling, groups and overall from the signal series, the muted cut
to the shared length, 3-decimal rounding and the series order. The golden fixture is generated
from the real code in step 3:

    python -c "from tests.test_response import write_golden; write_golden()"

The masks themselves are tested in tests/test_m3d_hcp.py; here they are built by hand.
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

# The golden atlas: vertex v carries area id v % 16 (0 = no area); each signal owns these ids.
GOLDEN_AREAS = {
    "faces": [1],
    "bodies": [2, 3],
    "scenes": [4],
    "visual": [5, 6, 7, 8],
    "sound": [9, 10],
    "speech": [11, 12, 13, 14],
    "social": [15],
}
SIGNAL_ORDER = ["faces", "bodies", "scenes", "visual", "sound", "speech", "social"]
EXPECTED_SERIES = [*SIGNAL_ORDER, "see", "hear", "understand", "overall"]


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
    areas = np.arange(GOLDEN_RECIPE["n_vertices"]) % 16
    masks = {signal: np.isin(areas, ids) for signal, ids in GOLDEN_AREAS.items()}
    rng = np.random.default_rng(GOLDEN_RECIPE["seed"])
    full = rng.standard_normal((GOLDEN_RECIPE["full_rows"], GOLDEN_RECIPE["n_vertices"]))
    noaudio = rng.standard_normal((GOLDEN_RECIPE["noaudio_rows"], GOLDEN_RECIPE["n_vertices"]))
    return full, noaudio, masks


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
