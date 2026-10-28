"""Experiment 3: time the GPU analysis pipeline on an on-premise-style machine.

This measures the same steps as the SageMaker worker's `_analyse`: build_events, predict with
audio, predict without audio, then extract_response. The model is loaded once, and one warm-up
clip is run before the measured runs and left out of the CSV.

    python experiments/tco_benchmark.py CLIP [CLIP ...] [--runs N] [--out PATH]

Writes a CSV with clip, video_seconds, run, total_seconds and gpu_name. No S3, database or AWS
calls. Heavy imports stay inside functions.
"""

import argparse
import csv
import shutil
import tempfile
import time
from pathlib import Path

from neurolens import inference, media
from neurolens.response import extract_response
from neurolens.worker import job_temp_dir

CSV_COLUMNS = ["clip", "video_seconds", "run", "total_seconds", "gpu_name"]


def analyse(path, duration, masks, feature_cache):
    """One pass of the worker's pipeline, without any storage or billing side effects.

    Like the worker, each run reads a fresh copy of the clip in its own temporary folder, so no
    run reuses features cached by an earlier run of the same file.
    """
    with job_temp_dir(feature_cache) as folder:
        copy = folder / f"input{path.suffix}"
        shutil.copyfile(path, copy)
        _run_pipeline(copy, duration, masks)


def _run_pipeline(path, duration, masks):
    events = inference.build_events(path)
    full = inference.predict(events, duration)
    muted = inference.predict(inference.without_audio(events), duration)
    extract_response(full, muted, masks)


def benchmark_clip(clip, runs, masks, feature_cache):
    """Run one clip `runs` times and return its measured rows, with the duration probed once."""
    duration = media.probe_duration(clip)
    gpu = inference.gpu_info()
    gpu_name = gpu["device"] if gpu is not None else ""
    rows = []
    for run in range(1, runs + 1):
        started = time.monotonic()
        analyse(clip, duration, masks, feature_cache)
        total_seconds = time.monotonic() - started
        rows.append(
            {
                "clip": clip.name,
                "video_seconds": round(duration, 3),
                "run": run,
                "total_seconds": round(total_seconds, 3),
                "gpu_name": gpu_name,
            }
        )
        print(f"  {clip.name} run {run}/{runs}: {total_seconds:.1f} s")
    return rows


def write_csv(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Time the analysis pipeline on this GPU machine.")
    parser.add_argument("clips", nargs="+", metavar="CLIP", help="video files to time")
    parser.add_argument("--runs", type=int, default=3, help="measured runs per clip (default 3)")
    parser.add_argument(
        "--out", default="exp3_onprem.csv", help="CSV path (default exp3_onprem.csv)"
    )
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    clips = [Path(clip) for clip in args.clips]
    # As in the worker: features go to a temporary folder, never into ./models.
    feature_cache = tempfile.mkdtemp(prefix="neurolens-features-")
    inference.load_model(feature_cache=feature_cache)
    masks = inference.roi_masks()

    print(f"Warm-up (excluded): {clips[0].name}")
    analyse(clips[0], media.probe_duration(clips[0]), masks, feature_cache)

    rows = []
    for clip in clips:
        print(f"\n{clip.name}")
        rows += benchmark_clip(clip, args.runs, masks, feature_cache)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_csv(out, rows)
    print(f"\n{len(rows)} runs written to {out}")


if __name__ == "__main__":
    main()
