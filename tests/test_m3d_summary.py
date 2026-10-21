"""M3d §6-§7: results.summarise, the response config block and the CSV columns (pure Python)."""

import csv
import io
import json
import math

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
SUMMARY_KEYS = {"opening", "strong_moments", "weak_moments", "audio_dependence"}


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


def mean(values):
    return sum(values) / len(values)


# ---------------------------------------------------------------- opening


def test_opening_is_stronger_than_the_rest():
    opening = summary_for([0.0, 2.0, 2.0, 2.0, 0.0, 0.0, 0.0])["opening"]

    assert opening["value"] == pytest.approx(2.0, abs=1e-6)
    assert opening["rest"] == pytest.approx(0.0, abs=1e-6)
    assert opening["word"] == "stronger than the rest"


def test_opening_is_weaker_than_the_rest():
    opening = summary_for([0.0, -2.0, -2.0, -2.0, 0.0, 0.0, 0.0])["opening"]

    assert opening["value"] == pytest.approx(-2.0, abs=1e-6)
    assert opening["rest"] == pytest.approx(0.0, abs=1e-6)
    assert opening["word"] == "weaker than the rest"


def test_opening_is_about_the_same_as_the_rest():
    opening = summary_for([0.0, 0.0, 3.0, 0.0, 0.0, 3.0, 0.0])["opening"]

    assert opening["value"] == pytest.approx(1.0, abs=1e-6)
    assert opening["rest"] == pytest.approx(1.0, abs=1e-6)
    assert opening["word"] == "about the same as the rest"


def test_opening_exactly_on_the_band_edge_is_about_the_same():
    x = math.sqrt(47.25)
    overall = [0.0, 1.0, 1.0, 1.0, x, -x, 0.0]
    opening = summary_for(overall)["opening"]

    values = overall[1:]
    sd = math.sqrt(sum((value - mean(values)) ** 2 for value in values) / len(values))
    assert opening["value"] - opening["rest"] == pytest.approx(0.25 * sd, abs=1e-6)
    assert opening["word"] == "about the same as the rest"


@pytest.mark.parametrize(
    "overall, expected_value",
    [
        ([0.1], 0.1),
        ([0.1, 0.2], 0.2),
        ([0.1, 0.2, 0.3], 0.25),
        ([0.1, 0.2, 0.3, 0.4], 0.3),
    ],
    ids=["T=1", "T=2", "T=3", "T=4"],
)
def test_opening_rest_and_word_are_null_for_short_videos(overall, expected_value):
    opening = summary_for(overall)["opening"]

    assert opening["value"] == pytest.approx(expected_value, abs=1e-6)
    assert opening["rest"] is None
    assert opening["word"] is None


def test_opening_rest_and_word_are_null_for_a_flat_line():
    opening = summary_for([0.7] * 7)["opening"]

    assert opening["value"] == pytest.approx(0.7, abs=1e-6)
    assert opening["rest"] is None
    assert opening["word"] is None


# ---------------------------------------------------------------- moments


def test_summary_keys_are_the_four_new_ones():
    assert set(summary_for([0.0] * 7)) == SUMMARY_KEYS


def test_strong_pick_skips_the_second_best_window_next_to_the_best():
    summary = summary_for([0.0, 1.0, 1.0, 1.0, -1.0, -1.0, -1.0])

    assert summary["strong_moments"] == [
        {"start": 1, "end": 4, "value": pytest.approx(1.0, abs=1e-6)}
    ]


def test_two_strong_picks_keep_a_two_second_gap():
    summary = summary_for([0.0, 2.0, 2.0, 2.0, -1.0, -1.0, -1.0, 2.0, 2.0, 2.0])

    assert summary["strong_moments"] == [
        {"start": 1, "end": 4, "value": pytest.approx(2.0, abs=1e-6)},
        {"start": 7, "end": 10, "value": pytest.approx(2.0, abs=1e-6)},
    ]


def test_strong_tie_goes_to_the_earliest_window():
    summary = summary_for([0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])

    assert summary["strong_moments"] == [
        {"start": 1, "end": 4, "value": pytest.approx(1.0, abs=1e-6)}
    ]


def test_weak_pick_skips_the_second_lowest_window_next_to_the_lowest():
    summary = summary_for([0.0, -1.0, -1.0, -1.0, 1.0, 1.0, 1.0])

    assert summary["weak_moments"] == [
        {"start": 1, "end": 4, "value": pytest.approx(-1.0, abs=1e-6)}
    ]


def test_weak_picks_skip_windows_that_overlap_a_strong_moment():
    overall = [0.0, 10.0, 10.0, 10.0, -50.0, -50.0, 10.0, 10.0]
    summary = summary_for(overall)

    assert summary["strong_moments"][0] == {
        "start": 1,
        "end": 4,
        "value": pytest.approx(10.0, abs=1e-6),
    }
    assert summary["weak_moments"] == [
        {"start": 4, "end": 7, "value": pytest.approx(-30.0, abs=1e-6)}
    ]


def test_weak_picks_keep_a_two_second_gap_and_come_back_in_time_order():
    overall = [0.0, -2.0, -2.0, -2.0, 1.0, 1.0, 1.0, -2.0, -2.0, -2.0]
    summary = summary_for(overall)

    assert summary["weak_moments"] == [
        {"start": 1, "end": 4, "value": pytest.approx(-2.0, abs=1e-6)},
        {"start": 7, "end": 10, "value": pytest.approx(-2.0, abs=1e-6)},
    ]
    assert summary["weak_moments"] == sorted(summary["weak_moments"], key=lambda m: m["start"])


# ---------------------------------------------------------------- growing


def test_a_plateau_grows_to_its_edges():
    overall = [0.0, -2.0, 2.0, 2.0, 2.0, 2.0, 2.0, -2.0, -2.0, -2.0]
    summary = summary_for(overall)

    assert summary["strong_moments"] == [
        {"start": 2, "end": 7, "value": pytest.approx(2.0, abs=1e-6)}
    ]


def test_a_lone_spike_stays_three_seconds():
    summary = summary_for([0.0, 0.0, 0.0, 5.0, 0.0, 0.0, 0.0])

    assert summary["strong_moments"] == [
        {"start": 1, "end": 4, "value": pytest.approx(5 / 3, abs=1e-6)}
    ]


def test_growth_stops_at_the_ten_second_cap():
    overall = [0.0] + [2.0] * 15 + [-2.0] * 4
    summary = summary_for(overall)

    first = summary["strong_moments"][0]
    assert first["start"] == 1
    assert first["end"] == 11
    assert first["value"] == pytest.approx(2.0, abs=1e-6)


def test_growth_stops_at_the_gap_to_an_earlier_pick():
    overall = [0.0, 3.0, 3.0, 3.0, 3.0, -3.0, 3.0, 3.0, 3.0, 3.0]
    summary = summary_for(overall)

    assert summary["strong_moments"] == [
        {"start": 1, "end": 5, "value": pytest.approx(3.0, abs=1e-6)},
        {"start": 7, "end": 10, "value": pytest.approx(3.0, abs=1e-6)},
    ]


def test_weak_growth_never_enters_a_strong_moment():
    overall = [0.0, 0.0, 0.0, -100.0, 50.0, 50.0, -50.0, -20.0, -20.0, -20.0]
    summary = summary_for(overall)

    assert summary["strong_moments"] == [
        {"start": 4, "end": 7, "value": pytest.approx(50 / 3, abs=1e-6)}
    ]
    # Without the strong moment's block, the second weak pick would grow into second 6.
    assert summary["weak_moments"] == [
        {"start": 1, "end": 4, "value": pytest.approx(-100 / 3, abs=1e-6)},
        {"start": 7, "end": 10, "value": pytest.approx(-20.0, abs=1e-6)},
    ]


# ---------------------------------------------------------------- the lengths


@pytest.mark.parametrize(
    "overall, expected",
    [
        (
            [5.0],
            {
                "opening": {"value": 5.0, "rest": None, "word": None},
                "strong_moments": [{"start": 0, "end": 1, "value": 5.0}],
                "weak_moments": [],
                "audio_dependence": 0.0,
            },
        ),
        (
            [9.0, 1.0],
            {
                "opening": {"value": 1.0, "rest": None, "word": None},
                "strong_moments": [{"start": 1, "end": 2, "value": 1.0}],
                "weak_moments": [],
                "audio_dependence": 0.0,
            },
        ),
        (
            [9.0, 1.0, 3.0],
            {
                "opening": {"value": 2.0, "rest": None, "word": None},
                "strong_moments": [{"start": 1, "end": 3, "value": 2.0}],
                "weak_moments": [],
                "audio_dependence": 0.0,
            },
        ),
        (
            [9.0, 1.0, 3.0, 5.0],
            {
                "opening": {"value": 3.0, "rest": None, "word": None},
                "strong_moments": [{"start": 1, "end": 4, "value": 3.0}],
                "weak_moments": [],
                "audio_dependence": 0.0,
            },
        ),
        (
            [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            {
                "opening": {"value": 2.0, "rest": 5.0, "word": "weaker than the rest"},
                "strong_moments": [{"start": 4, "end": 7, "value": 5.0}],
                "weak_moments": [{"start": 1, "end": 4, "value": 2.0}],
                "audio_dependence": 0.0,
            },
        ),
    ],
    ids=["T=1", "T=2", "T=3", "T=4", "T=7"],
)
def test_the_short_and_ordinary_lengths(overall, expected):
    summary = summary_for(overall)

    assert summary["opening"]["value"] == pytest.approx(expected["opening"]["value"], abs=1e-6)
    assert summary["opening"]["rest"] == expected["opening"]["rest"]
    assert summary["opening"]["word"] == expected["opening"]["word"]
    for key in ("strong_moments", "weak_moments"):
        assert len(summary[key]) == len(expected[key])
        for actual, wanted in zip(summary[key], expected[key], strict=True):
            assert actual["start"] == wanted["start"]
            assert actual["end"] == wanted["end"]
            assert actual["value"] == pytest.approx(wanted["value"], abs=1e-6)
    assert summary["audio_dependence"] == pytest.approx(expected["audio_dependence"], abs=1e-6)


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
