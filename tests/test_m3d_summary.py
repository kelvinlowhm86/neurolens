"""M3d §6-§7: results.summarise, the response config block and the CSV columns (pure Python)."""

import csv
import io
import json

import pytest
from neurolens import results
from neurolens.web.app import create_app

SERIES = [
    "faces",
    "bodies",
    "scenes",
    "visual",
    "sound",
    "speech",
    "social",
    "see",
    "hear",
    "understand",
    "overall",
]
COLUMNS = ["t", *SERIES, *(f"muted_{name}" for name in SERIES)]
CFG = {
    "well_above": 0.5,
    "above": 0.2,
    "below": -0.2,
    "dead_threshold": 0.2,
    "dead_min_seconds": 3,
    "colour_range": 0.5,
}


def zeros(length):
    return [0.0] * length


def make_result(overall, *, see=None, hear=None, understand=None, signals=None, muted=None):
    T = len(overall)
    series = {name: zeros(T) for name in SERIES}
    series.update(
        {
            "see": list(see) if see is not None else zeros(T),
            "hear": list(hear) if hear is not None else zeros(T),
            "understand": list(understand) if understand is not None else zeros(T),
            "overall": list(overall),
        }
    )
    if signals:
        series.update(signals)
    muted_series = {name: zeros(T) for name in SERIES}
    muted_series["overall"] = list(overall if muted is None else muted)
    return {
        "format": 2,
        "duration_seconds": T,
        "signals": series,
        "muted": muted_series,
    }


def summary_for(overall, **kwargs):
    return results.summarise(make_result(overall, **kwargs), CFG)


# ---------------------------------------------------------------- hook


def test_hook_uses_the_first_three_seconds_and_the_highest_group_mean():
    summary = summary_for([0.0, 0.0, 0.0], see=[0.0, 0.0, 3.0], hear=[0.8, 0.8, 0.8])
    assert summary["hook"] == {"group": "see", "value": 1.0, "level": "well above typical"}


def test_hook_uses_fewer_seconds_for_a_short_video():
    summary = summary_for([0.0, 0.0], see=[1.0, 0.0], hear=[0.8, 0.8])
    assert summary["hook"] == {"group": "hear", "value": 0.8, "level": "well above typical"}


def test_hook_with_a_one_second_video():
    summary = summary_for([0.0], see=[1.0], hear=[0.8])
    assert summary["hook"] == {"group": "see", "value": 1.0, "level": "well above typical"}


# ---------------------------------------------------------------- peak


@pytest.mark.parametrize(
    "overall, expected_t, expected_value, expected_level",
    [
        ([0.7], 0, 0.7, "well above typical"),
        ([0.9, 0.3], 1, 0.3, "above typical"),
        ([0.9, 0.2, 0.5], 2, 0.5, "well above typical"),
    ],
    ids=["T=1 includes zero", "T=2 skips zero", "T=3 skips zero"],
)
def test_peak_skips_second_zero_except_for_a_one_second_video(
    overall, expected_t, expected_value, expected_level
):
    peak = summary_for(overall)["peak"]
    assert peak == {"t": expected_t, "value": expected_value, "level": expected_level}


def test_peak_tie_goes_to_the_earliest_second():
    assert summary_for([0.1, 0.4, 0.4])["peak"] == {
        "t": 1,
        "value": 0.4,
        "level": "above typical",
    }


@pytest.mark.parametrize(
    "value, level",
    [
        (0.5, "well above typical"),
        (0.499, "above typical"),
        (0.2, "above typical"),
        (0.199, "typical"),
        (-0.199, "typical"),
        (-0.2, "below typical"),
        (-0.5, "below typical"),
    ],
)
def test_level_words_use_the_configured_thresholds(value, level):
    assert summary_for([value])["peak"]["level"] == level


# ---------------------------------------------------------------- dead stretches


def test_a_dead_stretch_at_the_very_start():
    summary = summary_for([-0.3, -0.3, -0.3, 0.1])
    assert summary["dead"] == [{"start": 0, "end": 3}]


def test_a_dead_stretch_at_the_very_end():
    summary = summary_for([0.1, -0.3, -0.3, -0.3])
    assert summary["dead"] == [{"start": 1, "end": 4}]


def test_a_dead_stretch_exactly_dead_min_seconds_long_counts():
    assert summary_for([0.1, -0.3, -0.3, -0.3, 0.1])["dead"] == [{"start": 1, "end": 4}]


def test_a_dead_stretch_one_second_too_short_does_not_count():
    assert summary_for([0.1, -0.3, -0.3, 0.1])["dead"] == []


def test_every_dead_stretch_is_reported():
    summary = summary_for([0.0, -0.3, -0.3, -0.3, 0.0, -0.3, -0.3, -0.3])
    assert summary["dead"] == [{"start": 1, "end": 4}, {"start": 5, "end": 8}]


def test_a_value_exactly_at_the_threshold_is_not_dead():
    assert summary_for([-0.2, -0.2, -0.2])["dead"] == []


# ---------------------------------------------------------------- audio dependence


def test_audio_dependence_is_one_when_muted_has_no_positive_response():
    assert summary_for([1.0, 1.0], muted=[0.0, 0.0])["audio_dependence"] == 1.0


def test_audio_dependence_is_the_positive_area_ratio():
    assert summary_for([1.0, 1.0], muted=[0.5, 0.5])["audio_dependence"] == 0.5


def test_audio_dependence_uses_the_shared_length():
    assert summary_for([1.0, 1.0, 1.0], muted=[0.5])["audio_dependence"] == 0.5


def test_audio_dependence_is_null_without_a_positive_response():
    assert summary_for([0.0, -1.0, 0.0], muted=[1.0, 1.0])["audio_dependence"] is None


def test_audio_dependence_is_null_below_the_denominator_floor():
    assert summary_for([5e-7, 0.0], muted=[0.0, 0.0])["audio_dependence"] is None


def test_audio_dependence_is_clipped_to_zero():
    assert summary_for([1.0], muted=[2.0])["audio_dependence"] == 0.0


def test_audio_dependence_is_rounded_to_two_decimals():
    assert summary_for([1.0, 1.0, 1.0], muted=[1.0, 1.0, 0.0])["audio_dependence"] == 0.33


# ---------------------------------------------------------------- config validation


def block(**overrides):
    cfg = dict(CFG)
    cfg.update(overrides)
    return cfg


def test_a_valid_response_block_is_accepted():
    assert results.validate_response_config(CFG) is None


@pytest.mark.parametrize("key", list(CFG))
@pytest.mark.parametrize("value", ["0.5", None, [0.5]])
def test_every_setting_must_be_a_number(key, value):
    with pytest.raises(ValueError):
        results.validate_response_config(block(**{key: value}))


@pytest.mark.parametrize(
    "overrides",
    [
        {"well_above": 0.2, "above": 0.2},
        {"well_above": 0.1, "above": 0.2},
        {"above": -0.2, "below": -0.2},
        {"above": -0.3, "below": -0.2},
    ],
)
def test_the_level_thresholds_must_strictly_descend(overrides):
    with pytest.raises(ValueError):
        results.validate_response_config(block(**overrides))


@pytest.mark.parametrize("value", [0, -0.1])
def test_dead_threshold_must_be_positive(value):
    with pytest.raises(ValueError):
        results.validate_response_config(block(dead_threshold=value))


@pytest.mark.parametrize("value", [0, -1, 1.5, "3"])
def test_dead_min_seconds_must_be_an_integer_of_at_least_one(value):
    with pytest.raises(ValueError):
        results.validate_response_config(block(dead_min_seconds=value))


@pytest.mark.parametrize("value", [0, -1])
def test_colour_range_must_be_positive(value):
    with pytest.raises(ValueError):
        results.validate_response_config(block(colour_range=value))


def test_a_missing_setting_is_refused():
    cfg = block()
    del cfg["colour_range"]
    with pytest.raises(ValueError):
        results.validate_response_config(cfg)


def test_create_app_refuses_a_bad_response_block(make_cfg, tmp_path):
    cfg = make_cfg(with_aws=False, response=block(dead_min_seconds=0))
    with pytest.raises(ValueError):
        create_app(cfg=cfg, data_dir=tmp_path / "data")


# ---------------------------------------------------------------- CSV


def parse_csv(text):
    return list(csv.reader(io.StringIO(text)))


def csv_result():
    T = 3
    signals = {
        name: [round(0.1 * (t + 1) + index, 3) for t in range(T)]
        for index, name in enumerate(SERIES)
    }
    muted = {
        name: [round(1.0 * (t + 1) + index, 3) for t in range(2)]
        for index, name in enumerate(SERIES)
    }
    return {
        "format": 2,
        "duration_seconds": T,
        "signals": signals,
        "muted": muted,
    }


def test_csv_columns_are_t_then_the_11_series_then_the_muted_series():
    assert results.COLUMNS == COLUMNS
    rows = parse_csv(results.to_csv(csv_result()))
    assert rows[0] == COLUMNS


def test_csv_has_one_row_per_second_and_empty_muted_cells_beyond_the_shared_length():
    result = csv_result()
    rows = parse_csv(results.to_csv(result))
    assert len(rows) == 1 + result["duration_seconds"]
    muted_columns = COLUMNS[1 + len(SERIES) :]
    for t, row in enumerate(rows[1:]):
        assert row[0] == str(t)
        assert len(row) == len(COLUMNS)
        unmuted = row[1 : 1 + len(SERIES)]
        assert all(cell != "" for cell in unmuted)
        if t < len(result["muted"]["faces"]):
            expected = [json.dumps(result["muted"][name][t]) for name in SERIES]
        else:
            expected = [""] * len(SERIES)
        assert row[1 + len(SERIES) :] == expected
        assert len(muted_columns) == len(expected)
