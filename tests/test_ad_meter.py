"""The Ad Meter pilot's pure functions and both scripts' shared behaviour."""

import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS = REPO_ROOT / "experiments"
BRAIN_KEYS = [
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


def load(name):
    spec = importlib.util.spec_from_file_location(f"experiment_{name}", EXPERIMENTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ANALYSE = load("ad_meter_analyse")
BASELINE = load("ad_meter_baseline")


# ---------------------------------------------------------------- auc


def test_auc_perfect_separation_is_one():
    assert ANALYSE.auc([3, 4, 5], [0, 1, 2]) == 1.0


def test_auc_fully_reversed_is_zero():
    assert ANALYSE.auc([0, 1, 2], [3, 4, 5]) == 0.0


def test_auc_all_ties_is_half():
    assert ANALYSE.auc([2, 2], [2, 2]) == 0.5


def test_auc_mixed_case_worked_by_hand():
    # (2>1) win, (2<3) loss, (4>1) win, (4>3) win: 3 of 4 pairs.
    assert ANALYSE.auc([2, 4], [1, 3]) == 0.75


# ---------------------------------------------------------------- verdict


def test_verdict_no_signal_below_point_six():
    assert ANALYSE.verdict(0.59, 0.50) == "No signal"


def test_verdict_unclear_at_point_six_edge():
    assert ANALYSE.verdict(0.60, 0.50) == "Unclear"


def test_verdict_unclear_between_point_six_and_point_seven():
    assert ANALYSE.verdict(0.65, 0.30) == "Unclear"


def test_verdict_real_hint_at_point_seven_with_lower_baseline():
    assert ANALYSE.verdict(0.70, 0.65) == "Real hint"


def test_verdict_real_hint_well_above_baseline():
    assert ANALYSE.verdict(0.80, 0.60) == "Real hint"


def test_verdict_adds_nothing_when_baseline_is_higher():
    assert ANALYSE.verdict(0.75, 0.85) == "Adds nothing over simple measures"


def test_verdict_adds_nothing_when_main_equals_baseline_at_or_above_point_seven():
    assert ANALYSE.verdict(0.70, 0.70) == "Adds nothing over simple measures"


# ---------------------------------------------------------------- permutation_p


def test_permutation_p_of_perfectly_separated_groups_is_small():
    p_value = ANALYSE.permutation_p([6, 7, 8, 9, 10], [1, 2, 3, 4, 5], shuffles=1000)
    assert p_value < 0.02


def test_permutation_p_of_identical_groups_is_near_one():
    p_value = ANALYSE.permutation_p([3, 3, 3, 3, 3], [3, 3, 3, 3, 3], shuffles=100)
    assert p_value > 0.9


def test_permutation_p_is_repeatable_with_the_same_seed():
    first = ANALYSE.permutation_p([6, 7, 8, 9, 10], [1, 2, 3, 4, 5], shuffles=50, seed=7)
    second = ANALYSE.permutation_p([6, 7, 8, 9, 10], [1, 2, 3, 4, 5], shuffles=50, seed=7)
    assert first == second


# ---------------------------------------------------------------- analyse end to end


def write_brain(path, overall):
    signals = {name: [0.0] * len(overall) for name in BRAIN_KEYS}
    signals["overall"] = overall
    payload = {
        "format": 2,
        "duration_seconds": len(overall),
        "signals": signals,
        "muted": signals,
        "words": [],
    }
    path.write_text(json.dumps(payload))


def make_dataset(tmp_path):
    brain = tmp_path / "brain"
    brain.mkdir()
    ads = []
    for index, clip_name in enumerate(["top_a", "top_b", "top_c"], start=1):
        ads.append(
            {
                "rank": str(index),
                "score": "4.00",
                "group": "top",
                "brand": f"Brand {index}",
                "title": f"Top {index}",
                "youtube_id": f"top{index}",
                "aired_seconds": "30",
                "clip": clip_name,
            }
        )
        write_brain(brain / f"{clip_name}.json", [1.0] * 6)
    for index, clip_name in enumerate(["bottom_a", "bottom_b", "bottom_c"], start=4):
        ads.append(
            {
                "rank": str(index),
                "score": "2.00",
                "group": "bottom",
                "brand": f"Brand {index}",
                "title": f"Bottom {index}",
                "youtube_id": f"bottom{index}",
                "aired_seconds": "30",
                "clip": clip_name,
            }
        )
        write_brain(brain / f"{clip_name}.json", [-1.0] * 6)

    ads_path = tmp_path / "ads.csv"
    with ads_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ads[0]))
        writer.writeheader()
        writer.writerows(ads)

    baseline_path = tmp_path / "baseline.csv"
    with baseline_path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "clip",
                "duration_seconds",
                "face_share",
                "mean_volume_db",
                "cuts_per_second",
            ],
        )
        writer.writeheader()
        for ad in ads:
            # Constant simple measures: their direction-free AUC is 0.5.
            writer.writerow(
                {
                    "clip": ad["clip"],
                    "duration_seconds": "30.0",
                    "face_share": "0.5",
                    "mean_volume_db": "-20.0",
                    "cuts_per_second": "0.2",
                }
            )

    return ads_path, brain, baseline_path, ads


def run_analyse(ads_path, brain, baseline_path, out):
    return subprocess.run(
        [
            sys.executable,
            str(EXPERIMENTS / "ad_meter_analyse.py"),
            "--ads",
            str(ads_path),
            "--brain",
            str(brain),
            "--baseline",
            str(baseline_path),
            "--out",
            str(out),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_analyse_writes_a_report_with_the_expected_verdict(tmp_path):
    ads_path, brain, baseline_path, _ = make_dataset(tmp_path)
    out = tmp_path / "report" / "verdict.md"

    result = run_analyse(ads_path, brain, baseline_path, out)

    assert result.returncode == 0, result.stderr
    assert out.exists()
    assert "Real hint" in out.read_text()


def test_analyse_refuses_a_missing_brain_json(tmp_path):
    ads_path, brain, baseline_path, _ = make_dataset(tmp_path)
    (brain / "top_a.json").unlink()
    out = tmp_path / "report" / "verdict.md"

    result = run_analyse(ads_path, brain, baseline_path, out)

    assert result.returncode != 0
    assert "top_a" in result.stderr
    assert not out.exists()


# ---------------------------------------------------------------- ffmpeg parsing


def test_parse_mean_volume_reads_volumedetect_stderr():
    stderr = (
        "[Parsed_volumedetect_0 @ 0x7f8] n_samples: 1323000\n"
        "[Parsed_volumedetect_0 @ 0x7f8] mean_volume: -23.4 dB\n"
        "[Parsed_volumedetect_0 @ 0x7f8] max_volume: -3.0 dB\n"
    )
    assert BASELINE.parse_mean_volume(stderr) == -23.4


def test_parse_mean_volume_without_the_line_raises():
    try:
        BASELINE.parse_mean_volume("[Parsed_volumedetect_0 @ 0x7f8] max_volume: -3.0 dB\n")
    except ValueError:
        pass
    else:
        raise AssertionError("a missing mean_volume line must raise ValueError")


def test_count_cuts_counts_showinfo_frame_lines():
    stderr = (
        "[Parsed_showinfo_1 @ 0x7f8] n:   0 pts:  1000 pts_time:0.033 "
        "duration:1000 pos:1234 fmt:yuv420p\n"
        "[Parsed_showinfo_1 @ 0x7f8] n:   1 pts: 40000 pts_time:1.234 "
        "duration:1000 pos:5678 fmt:yuv420p\n"
    )
    assert BASELINE.count_cuts(stderr) == 2


def test_count_cuts_is_zero_without_showinfo_frames():
    assert BASELINE.count_cuts("[Parsed_showinfo_1 @ 0x7f8] config in time_base: 1/30\n") == 0
