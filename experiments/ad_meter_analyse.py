"""Does NeuroLens separate well-liked from disliked ads? The Ad Meter pilot's fixed verdict.

    python experiments/ad_meter_analyse.py --ads CSV --brain DIR --baseline CSV --out REPORT.md

Every ad needs a brain JSON and a baseline row; a partial set never gets a verdict. The main
score is the mean of the with-sound `overall` series, tested only in the pre-set direction
(higher = better liked). The four simple measures are tested direction-free, so the comparison
is strict on NeuroLens. numpy and the standard library only.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

MAIN_SIGNAL = "overall"
SECONDARY_SIGNALS = ["faces", "bodies", "scenes", "visual", "sound", "speech", "social"]
BASELINE_FEATURES = ["duration_seconds", "face_share", "mean_volume_db", "cuts_per_second"]
SCORE_ROWS = [
    ("overall_mean", "main"),
    ("share_above_typical", "secondary"),
    ("first5_mean", "secondary"),
    *[(name, "secondary") for name in SECONDARY_SIGNALS],
]


def mean(values):
    return sum(values) / len(values)


def auc(top_values, bottom_values):
    """Share of (top, bottom) pairs where the top value is higher; a tie counts half."""
    wins = 0.0
    for top in top_values:
        for bottom in bottom_values:
            if top > bottom:
                wins += 1.0
            elif top == bottom:
                wins += 0.5
    return wins / (len(top_values) * len(bottom_values))


def permutation_p(top_values, bottom_values, shuffles=10000, seed=0):
    """One-sided p-value for "top scores higher", from shuffled group labels."""
    rng = np.random.default_rng(seed)
    pool = np.asarray(list(top_values) + list(bottom_values), dtype=float)
    n_top = len(top_values)
    observed = auc(top_values, bottom_values)
    at_least = 0
    for _ in range(shuffles):
        shuffled = rng.permutation(pool)
        if auc(shuffled[:n_top], shuffled[n_top:]) >= observed:
            at_least += 1
    return (at_least + 1) / (shuffles + 1)


def verdict(main_auc, best_baseline_auc):
    """The pre-registered rule. It is deliberately asymmetric: the main score is only tested in
    the pre-set direction, while each simple measure gets its better direction, which is strict
    on NeuroLens."""
    if main_auc < 0.60:
        return "No signal"
    if main_auc < 0.70:
        return "Unclear"
    if main_auc > best_baseline_auc:
        return "Real hint"
    return "Adds nothing over simple measures"


def read_ads(path):
    with Path(path).open(newline="") as handle:
        return list(csv.DictReader(handle))


def read_baselines(path):
    with Path(path).open(newline="") as handle:
        return {row["clip"]: row for row in csv.DictReader(handle)}


def score_ad(signals):
    """The main score plus every secondary score from the with-sound pass."""
    overall = [float(value) for value in signals[MAIN_SIGNAL]]
    scores = {
        "overall_mean": mean(overall),
        "share_above_typical": sum(1 for value in overall if value > 0) / len(overall),
        "first5_mean": mean(overall[:5]),
    }
    for name in SECONDARY_SIGNALS:
        scores[name] = mean([float(value) for value in signals[name]])
    return scores


def missing_inputs(ads, brain_dir, baselines):
    """Every missing brain JSON or baseline row, named by clip."""
    missing = []
    for ad in ads:
        clip = ad["clip"]
        if not (Path(brain_dir) / f"{clip}.json").exists():
            missing.append(f"brain response for {clip}")
        if clip not in baselines:
            missing.append(f"baseline row for {clip}")
    return missing


def build_report(ads, tops, bottoms, baselines, brain_dir):
    """Rows of numbers for both groups and the finished Markdown report."""
    for ad in ads:
        scores = score_ad(
            json.loads((Path(brain_dir) / f"{ad['clip']}.json").read_text())["signals"]
        )
        ad["scores"] = scores
        ad["baseline"] = baselines[ad["clip"]]

    def values(group, getter):
        return [getter(ad) for ad in group]

    main_auc = auc(
        values(tops, lambda ad: ad["scores"]["overall_mean"]),
        values(bottoms, lambda ad: ad["scores"]["overall_mean"]),
    )
    p_value = permutation_p(
        values(tops, lambda ad: ad["scores"]["overall_mean"]),
        values(bottoms, lambda ad: ad["scores"]["overall_mean"]),
    )

    baseline_rows = []
    for feature in BASELINE_FEATURES:
        directional = auc(
            values(tops, lambda ad, f=feature: float(ad["baseline"][f])),
            values(bottoms, lambda ad, f=feature: float(ad["baseline"][f])),
        )
        top_mean = mean(values(tops, lambda ad, f=feature: float(ad["baseline"][f])))
        bottom_mean = mean(values(bottoms, lambda ad, f=feature: float(ad["baseline"][f])))
        higher = "top" if top_mean > bottom_mean else "bottom" if top_mean < bottom_mean else "tie"
        baseline_rows.append(
            {
                "feature": feature,
                "direction_free_auc": max(directional, 1 - directional),
                "higher": higher,
                "top_mean": top_mean,
                "bottom_mean": bottom_mean,
            }
        )
    best = max(baseline_rows, key=lambda row: row["direction_free_auc"])
    outcome = verdict(main_auc, best["direction_free_auc"])

    lines = [
        "# Ad Meter 2026: NeuroLens vs simple measures",
        "",
        f"Verdict: **{outcome}**",
        "",
        f"- Main AUC: {main_auc:.3f}",
        f"- One-sided permutation p-value: {p_value:.4f}",
        (
            f"- Best simple measure: {best['feature']} "
            f"(direction-free AUC {best['direction_free_auc']:.3f})"
        ),
        f"- Group sizes: {len(tops)} top, {len(bottoms)} bottom",
        "",
        "## NeuroLens scores",
        "",
        "| Score | AUC | Mean top | Mean bottom |",
        "| --- | --- | --- | --- |",
    ]
    for name, kind in SCORE_ROWS:
        score_auc = auc(
            values(tops, lambda ad, n=name: ad["scores"][n]),
            values(bottoms, lambda ad, n=name: ad["scores"][n]),
        )
        lines.append(
            f"| {name} ({kind}) | {score_auc:.3f} | "
            f"{mean(values(tops, lambda ad, n=name: ad['scores'][n])):.4f} | "
            f"{mean(values(bottoms, lambda ad, n=name: ad['scores'][n])):.4f} |"
        )

    lines += [
        "",
        "## Simple measures",
        "",
        "| Feature | Direction-free AUC | Higher group | Mean top | Mean bottom |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in baseline_rows:
        lines.append(
            f"| {row['feature']} | {row['direction_free_auc']:.3f} | {row['higher']} | "
            f"{row['top_mean']:.4f} | {row['bottom_mean']:.4f} |"
        )

    lines += [
        "",
        "## Ads",
        "",
        "| Rank | Brand | Title | Group | Ad Meter score | overall_mean | duration_seconds |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for ad in sorted(ads, key=lambda item: int(item["rank"])):
        lines.append(
            f"| {ad['rank']} | {ad['brand']} | {ad['title']} | {ad['group']} | {ad['score']} | "
            f"{ad['scores']['overall_mean']:.4f} | "
            f"{float(ad['baseline']['duration_seconds']):.3f} |"
        )

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Ad Meter pilot: the pre-registered verdict.")
    parser.add_argument("--ads", required=True, help="ads CSV (rank, score, group, ...)")
    parser.add_argument("--brain", required=True, help="directory of brain response JSONs")
    parser.add_argument("--baseline", required=True, help="baseline CSV from ad_meter_baseline.py")
    parser.add_argument("--out", required=True, help="Markdown report path")
    args = parser.parse_args()

    ads = read_ads(args.ads)
    baselines = read_baselines(args.baseline)
    missing = missing_inputs(ads, args.brain, baselines)
    if missing:
        print("Cannot analyse a partial set. Missing:", file=sys.stderr)
        for item in missing:
            print(f"  {item}", file=sys.stderr)
        sys.exit(1)

    unknown = [ad["clip"] for ad in ads if ad["group"] not in ("top", "bottom")]
    if unknown:
        sys.exit(f"Cannot analyse: group must be top or bottom for {', '.join(unknown)}")
    tops = [ad for ad in ads if ad["group"] == "top"]
    bottoms = [ad for ad in ads if ad["group"] == "bottom"]
    if not tops or not bottoms:
        sys.exit("Cannot analyse: both groups must be non-empty")

    report = build_report(ads, tops, bottoms, baselines, args.brain)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(out.name + ".tmp")
    temporary.write_text(report)
    temporary.replace(out)
    print(report, end="")


if __name__ == "__main__":
    main()
