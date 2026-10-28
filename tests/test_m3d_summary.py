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
CFG = {"colour_range": 0.5}
SUMMARY_KEYS = {"audio_dependence"}


def zeros(length):
    return [0.0] * length


def make_result(overall, muted=None):
    T = len(overall)
    signals = {name: zeros(T) for name in SERIES}
    signals["overall"] = list(overall)
    muted_signals = {name: zeros(T) for name in SERIES}
    muted_signals["overall"] = list(overall if muted is None else muted)
    return {
        "format": 2,
        "duration_seconds": T,
        "signals": signals,
        "muted": muted_signals,
    }


def summary_for(overall, **kwargs):
    return results.summarise(make_result(overall, **kwargs))


# ---------------------------------------------------------------- summary


def test_summary_has_only_audio_dependence():
    assert set(summary_for([0.0] * 7)) == SUMMARY_KEYS


# ---------------------------------------------------------------- audio dependence


def test_audio_dependence_is_one_when_muted_has_no_positive_response():
    assert summary_for([1.0, 1.0], muted=[0.0, 0.0])["audio_dependence"] == 1.0


def test_audio_dependence_is_the_positive_area_ratio_from_the_second_second():
    assert summary_for([1.0, 1.0], muted=[0.5, 0.5])["audio_dependence"] == 0.5


def test_audio_dependence_uses_the_shared_length():
    assert summary_for([0.0, 1.0, 1.0, 1.0], muted=[9.0, 0.5])["audio_dependence"] == 0.5


def test_audio_dependence_is_null_without_a_positive_response():
    assert summary_for([0.0, -1.0, 0.0], muted=[1.0, 1.0])["audio_dependence"] is None


def test_audio_dependence_is_null_below_the_denominator_floor():
    assert summary_for([0.0, 5e-7], muted=[0.0, 0.0])["audio_dependence"] is None


def test_audio_dependence_is_clipped_to_zero():
    assert summary_for([0.0, 1.0], muted=[5.0, 2.0])["audio_dependence"] == 0.0


def test_audio_dependence_is_clipped_to_one():
    assert summary_for([0.0, 1.0], muted=[5.0, -2.0])["audio_dependence"] == 1.0


def test_audio_dependence_is_rounded_to_two_decimals():
    assert summary_for([0.0, 1.0, 1.0, 1.0], muted=[0.0, 1.0, 1.0, 0.0])["audio_dependence"] == 0.33


def test_audio_dependence_ignores_the_second_zero():
    without = summary_for([0.0, 1.0, 1.0], muted=[0.0, 0.5, 0.5])
    with_huge_zero = summary_for([1000.0, 1.0, 1.0], muted=[1000.0, 0.5, 0.5])

    assert with_huge_zero["audio_dependence"] == without["audio_dependence"] == 0.5


def test_the_second_zero_changes_nothing_when_the_video_is_longer():
    without = summary_for(
        [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        muted=[0.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0],
    )
    with_huge_zero = summary_for(
        [1000.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        muted=[1000.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0],
    )

    assert with_huge_zero == without


# ---------------------------------------------------------------- config validation


def test_a_valid_response_block_is_accepted():
    assert results.validate_response_config(CFG) is None


@pytest.mark.parametrize("value", [None, "0.5", True, False, [0.5], 0, -0.1])
def test_colour_range_must_be_a_positive_number(value):
    with pytest.raises(ValueError):
        results.validate_response_config({"colour_range": value})


def test_a_missing_colour_range_is_refused():
    with pytest.raises(ValueError):
        results.validate_response_config({})


def test_create_app_refuses_a_bad_response_block(make_cfg, tmp_path):
    cfg = make_cfg(with_aws=False, response={"colour_range": 0})
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
