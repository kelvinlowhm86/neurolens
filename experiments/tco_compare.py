"""Experiment 3: cloud vs on-premise cost comparison (estimate only).

    python experiments/tco_compare.py --cloud-csv experiment-1/runs.csv \
        --onprem-csv exp3_onprem.csv [--assumptions experiments/tco_assumptions.json] \
        [--out-dir .]

The cloud side uses the processing stage columns from Experiment 1's runs.csv. The on-premise
side uses the total pipeline seconds from experiments/tco_benchmark.py. Every price comes from
experiments/tco_assumptions.json. The result is an estimate from measured run times and stated
assumptions, not a bill.
"""

import argparse
import csv
import json
import statistics
from pathlib import Path

PROCESSING_MS_COLUMNS = (
    "transcribing_ms",
    "inference_full_ms",
    "inference_noaudio_ms",
    "extracting_roi_ms",
)
CLOUD_LENGTH_COLUMN = "clip_seconds"
ONPREM_LENGTH_COLUMN = "video_seconds"
CSV_COLUMNS = [
    "clip_seconds",
    "cloud_runs",
    "onprem_runs",
    "cloud_mean_run_seconds",
    "onprem_mean_run_seconds",
    "cloud_per_video_usd",
    "onprem_per_video_usd",
    "cloud_fixed_monthly_usd",
    "onprem_fixed_monthly_usd",
    "breakeven_videos_per_month",
    "onprem_capacity_videos_per_month",
    "capacity_warning",
]


def _number(assumptions, group, name):
    try:
        return float(assumptions[group][name]["value"])
    except (KeyError, TypeError, ValueError) as err:
        raise ValueError(
            f"Assumption {group}.{name} must have a numeric 'value' and its source fields"
        ) from err


def _clip_length(value):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError) as err:
        raise ValueError(f"Clip length is not a number: {value!r}") from err


def _group_means(rows, length_column, seconds_of_row):
    grouped = {}
    for row in rows:
        length = _clip_length(row[length_column])
        seconds = seconds_of_row(row)
        if seconds is not None:
            grouped.setdefault(length, []).append(seconds)
    return {length: (statistics.fmean(values), len(values)) for length, values in grouped.items()}


def _cloud_seconds(row):
    values = []
    for column in PROCESSING_MS_COLUMNS:
        value = row.get(column)
        if value in (None, ""):
            return None
        values.append(float(value))
    return sum(values) / 1000


def cloud_means(rows):
    """Mean processing seconds and run count per clip length from Experiment 1's runs.csv."""
    return _group_means(rows, CLOUD_LENGTH_COLUMN, _cloud_seconds)


def onprem_means(rows):
    """Mean total pipeline seconds and run count per clip length from tco_benchmark.py's CSV."""
    return _group_means(rows, ONPREM_LENGTH_COLUMN, lambda row: float(row["total_seconds"]))


def cloud_per_video(assumptions, mean_run_seconds):
    rate = _number(assumptions, "cloud", "gpu_usd_per_hour")
    cold = _number(assumptions, "cloud", "cold_start_minutes")
    idle = _number(assumptions, "cloud", "idle_wait_minutes")
    per_session = _number(assumptions, "cloud", "videos_per_cold_session")
    return mean_run_seconds * rate / 3600 + (cold + idle) * rate / 60 / per_session


def cloud_fixed_monthly(assumptions):
    return _number(assumptions, "cloud", "fixed_usd_per_month")


def onprem_fixed_monthly(assumptions):
    hardware = _number(assumptions, "on_prem", "gpu_price_usd") + _number(
        assumptions, "on_prem", "host_price_usd"
    )
    months = _number(assumptions, "on_prem", "amortisation_months")
    idle = _number(assumptions, "on_prem", "host_idle_watts")
    hours = _number(assumptions, "on_prem", "hours_on_per_month")
    electricity = _number(assumptions, "on_prem", "electricity_usd_per_kwh")
    return hardware / months + idle * hours / 1000 * electricity


def onprem_per_video(assumptions, mean_run_seconds):
    tdp = _number(assumptions, "on_prem", "gpu_tdp_watts")
    electricity = _number(assumptions, "on_prem", "electricity_usd_per_kwh")
    return tdp * mean_run_seconds / 3600 / 1000 * electricity


def breakeven_videos(cloud_per_video_usd, onprem_per_video_usd, cloud_fixed_usd, onprem_fixed_usd):
    """Videos per month where monthly costs meet, or None if cloud is never cheaper per video."""
    if cloud_per_video_usd <= onprem_per_video_usd:
        return None
    return (onprem_fixed_usd - cloud_fixed_usd) / (cloud_per_video_usd - onprem_per_video_usd)


def onprem_capacity(assumptions, mean_run_seconds):
    if mean_run_seconds <= 0:
        raise ValueError("On-prem mean run time must be positive")
    hours = _number(assumptions, "on_prem", "hours_on_per_month")
    return hours * 3600 / mean_run_seconds


def compare(assumptions, cloud_rows, onprem_rows):
    """One row per clip length present in both inputs, plus the lengths that were skipped."""
    cloud = cloud_means(cloud_rows)
    onprem = onprem_means(onprem_rows)
    cloud_fixed = cloud_fixed_monthly(assumptions)
    onprem_fixed = onprem_fixed_monthly(assumptions)
    rows = []
    skipped = []
    for length in sorted(set(cloud) | set(onprem)):
        if length not in cloud or length not in onprem:
            missing = [
                name
                for name, means in (("cloud", cloud), ("on-prem", onprem))
                if length not in means
            ]
            skipped.append({"clip_seconds": length, "missing": missing})
            continue
        cloud_mean, cloud_runs = cloud[length]
        onprem_mean, onprem_runs = onprem[length]
        cloud_cost = cloud_per_video(assumptions, cloud_mean)
        onprem_cost = onprem_per_video(assumptions, onprem_mean)
        breakeven = breakeven_videos(cloud_cost, onprem_cost, cloud_fixed, onprem_fixed)
        capacity = onprem_capacity(assumptions, onprem_mean)
        rows.append(
            {
                "clip_seconds": length,
                "cloud_runs": cloud_runs,
                "onprem_runs": onprem_runs,
                "cloud_mean_run_seconds": cloud_mean,
                "onprem_mean_run_seconds": onprem_mean,
                "cloud_per_video_usd": cloud_cost,
                "onprem_per_video_usd": onprem_cost,
                "cloud_fixed_monthly_usd": cloud_fixed,
                "onprem_fixed_monthly_usd": onprem_fixed,
                "breakeven_videos_per_month": breakeven,
                "onprem_capacity_videos_per_month": capacity,
                "capacity_warning": breakeven is not None and breakeven > capacity,
            }
        )
    return rows, skipped


def _csv_row(row):
    breakeven = row["breakeven_videos_per_month"]
    return {
        "clip_seconds": row["clip_seconds"],
        "cloud_runs": row["cloud_runs"],
        "onprem_runs": row["onprem_runs"],
        "cloud_mean_run_seconds": f"{row['cloud_mean_run_seconds']:.3f}",
        "onprem_mean_run_seconds": f"{row['onprem_mean_run_seconds']:.3f}",
        "cloud_per_video_usd": f"{row['cloud_per_video_usd']:.4f}",
        "onprem_per_video_usd": f"{row['onprem_per_video_usd']:.4f}",
        "cloud_fixed_monthly_usd": f"{row['cloud_fixed_monthly_usd']:.2f}",
        "onprem_fixed_monthly_usd": f"{row['onprem_fixed_monthly_usd']:.2f}",
        "breakeven_videos_per_month": "" if breakeven is None else f"{breakeven:.1f}",
        "onprem_capacity_videos_per_month": f"{row['onprem_capacity_videos_per_month']:.0f}",
        "capacity_warning": "yes" if row["capacity_warning"] else "no",
    }


def write_csv(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(_csv_row(row) for row in rows)


def write_markdown(path, rows, skipped, assumptions):
    lines = [
        "# Experiment 3: cloud vs on-premise cost",
        "",
        "All figures are an estimate from measured run times and stated assumptions.",
        "Cloud per-video cost uses Experiment 1's processing stage times; on-premise per-video",
        "cost uses the benchmark's total pipeline time.",
        "",
        "| clip (s) | cloud per video (USD) | on-prem per video (USD) | "
        "cloud fixed monthly (USD) | on-prem fixed monthly (USD) | "
        "break-even (videos/month) | on-prem capacity (videos/month) | note |",
        "|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        breakeven = row["breakeven_videos_per_month"]
        notes = []
        if breakeven is None:
            breakeven_text = "none"
            notes.append("cloud is not cheaper per video")
        else:
            breakeven_text = f"{breakeven:,.0f}"
        if row["capacity_warning"]:
            notes.append("break-even exceeds one GPU's capacity")
        lines.append(
            f"| {row['clip_seconds']} | {row['cloud_per_video_usd']:.4f} | "
            f"{row['onprem_per_video_usd']:.4f} | {row['cloud_fixed_monthly_usd']:.2f} | "
            f"{row['onprem_fixed_monthly_usd']:.2f} | {breakeven_text} | "
            f"{row['onprem_capacity_videos_per_month']:,.0f} | {'; '.join(notes)} |"
        )

    if skipped:
        lines += ["", "## Skipped clip lengths", ""]
        for item in skipped:
            missing = " and ".join(item["missing"])
            lines.append(f"- {item['clip_seconds']} s: no {missing} runs; skipped, never guessed.")

    lines += ["", "## Assumptions", ""]
    for group, title in (("cloud", "Cloud"), ("on_prem", "On-premise")):
        lines += [f"### {title}", ""]
        for name, entry in assumptions[group].items():
            marker = ", estimate" if entry.get("estimate") else ""
            lines.append(
                f"- `{name}` = {entry['value']} {entry['unit']} (source: {entry['source']}{marker})"
            )
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n")


def _read_rows(path):
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def main():
    parser = argparse.ArgumentParser(description="Compare cloud and on-premise costs (estimate).")
    parser.add_argument(
        "--assumptions",
        default="experiments/tco_assumptions.json",
        help="JSON with the prices and assumptions",
    )
    parser.add_argument("--cloud-csv", required=True, help="Experiment 1 runs.csv")
    parser.add_argument("--onprem-csv", required=True, help="CSV from tco_benchmark.py")
    parser.add_argument("--out-dir", default=".", help="where exp3_tco.csv and exp3_tco.md go")
    args = parser.parse_args()

    assumptions = json.loads(Path(args.assumptions).read_text())
    rows, skipped = compare(
        assumptions,
        _read_rows(Path(args.cloud_csv)),
        _read_rows(Path(args.onprem_csv)),
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "exp3_tco.csv", rows)
    write_markdown(out_dir / "exp3_tco.md", rows, skipped, assumptions)

    for item in skipped:
        print(f"Skipped {item['clip_seconds']} s: no {' or '.join(item['missing'])} runs")
    print(
        f"{len(rows)} clip length(s) compared; wrote {out_dir / 'exp3_tco.csv'} "
        f"and {out_dir / 'exp3_tco.md'}"
    )


if __name__ == "__main__":
    main()
