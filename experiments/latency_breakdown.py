"""Experiment 1 analysis (docs/M2b_spec.md §10): median, minimum and maximum of every stage, per
clip length, from experiment-1 runs.csv files.

    python experiments/latency_breakdown.py --run-id <run_id> [--run-id <run_id> ...]
"""

import argparse
import statistics

MS_COLUMNS = [
    "upload_ms",
    "queue_wait_ms",
    "downloading_ms",
    "transcribing_ms",
    "inference_full_ms",
    "inference_noaudio_ms",
    "extracting_roi_ms",
    "result_fetch_ms",
    "render_ms",
]


def summarise(rows):
    """One dict per clip length: clip_seconds, runs, and <column>_median / _min / _max of every
    *_ms column (empty values ignored; None when a column has no values). Pure."""
    by_clip = {}
    for row in rows:
        by_clip.setdefault(float(row["clip_seconds"]), []).append(row)
    out = []
    for clip in sorted(by_clip):
        group = by_clip[clip]
        entry = {"clip_seconds": int(clip) if clip == int(clip) else clip, "runs": len(group)}
        for col in MS_COLUMNS:
            values = [float(r[col]) for r in group if r.get(col) not in (None, "")]
            entry[f"{col}_median"] = statistics.median(values) if values else None
            entry[f"{col}_min"] = min(values) if values else None
            entry[f"{col}_max"] = max(values) if values else None
        out.append(entry)
    return out


def main():
    parser = argparse.ArgumentParser(description="Summarise Experiment 1 runs per clip length.")
    parser.add_argument("--run-id", action="append", required=True)
    args = parser.parse_args()

    import boto3

    from neurolens import experiment_runs, settings

    cfg = settings.load_settings()
    bucket = cfg["aws"]["s3_bucket"]
    s3 = boto3.client("s3", region_name=cfg["aws"]["region"])
    rows = []
    for run_id in args.run_id:
        rows += experiment_runs.read_csv_rows(
            experiment_runs.get_text(s3, bucket, "experiment-1", run_id, "runs.csv")
        )
    for entry in summarise(rows):
        print(f"{entry['clip_seconds']} s clip, {entry['runs']} runs (median [min-max] in s)")
        for col in MS_COLUMNS:
            if entry[f"{col}_median"] is not None:
                print(
                    f"  {col[:-3]:<18}{entry[f'{col}_median'] / 1000:8.1f} "
                    f"[{entry[f'{col}_min'] / 1000:.1f}-{entry[f'{col}_max'] / 1000:.1f}]"
                )


if __name__ == "__main__":
    main()
