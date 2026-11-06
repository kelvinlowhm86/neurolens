"""Does showing TRIBE v2 the whole frame change its brain signals? (crop vs pad)

TRIBE v2's video encoder (V-JEPA 2) resizes each frame's short edge and then centre-crops a
square, so a 16:9 video loses its left and right edges and a 9:16 video loses its top and bottom
(where TikTok captions sit). This script makes two re-encoded copies of each clip, one as is
and one letterboxed (padded with black bars to a square, nothing cut off), runs both through the
model and compares the seven signals and their groups.

    python experiments/crop_vs_pad.py CLIP [CLIP ...] --out DIR

Each clip gives DIR/<clip stem>.<crop|pad>.json with the response plus the clip name, variant,
frame size and seconds taken. Comparisons go to DIR/summary.csv. A finished clip+variant is
skipped on a rerun, so a job stopped by the time limit can be resumed with the same command.

FAKE_INFERENCE=1 works without a GPU or model weights (inference's fake mode).
"""

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from neurolens import inference, media, response
from neurolens.response import extract_response

VARIANTS = ("crop", "pad")
PASSES = ("signals", "muted")
COLUMNS = [
    "clip",
    "width",
    "height",
    "pass",
    "series",
    "r",
    "mean_crop",
    "mean_pad",
    "mean_abs_diff",
]
# Both variants are re-encoded the same way, so padding is the only difference between them.
# The square is 292/256 of the long side: V-JEPA 2 resizes the short edge to 292 and keeps the
# centre 256, so this margin keeps the whole picture. Commas inside max() are escaped for ffmpeg
# (one argument, no shell).
SIDE = "2*ceil(max(iw\\,ih)*292/256/2)"
FILTERS = {"crop": "null", "pad": f"pad={SIDE}:{SIDE}:(ow-iw)/2:(oh-ih)/2:black"}


def probe_dimensions(path):
    """Frame width and height from ffprobe, as integers."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    stream = json.loads(out.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def make_copy(clip, variant, out_dir):
    """Re-encode `clip` into `out_dir` with the variant's filter (pad: letterboxed to a square,
    picture centred), audio untouched. Returns the path of the copy."""
    copy = out_dir / f"{clip.stem}.{variant}.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(clip), "-vf", FILTERS[variant], "-c:a", "copy", str(copy)],
        check=True,
    )
    return copy


def run_variant(clip, path, variant, out_dir, masks):
    """Predict one variant and write its JSON. Nothing is written before the prediction finishes."""
    started = time.monotonic()
    duration = media.probe_duration(path)
    events = inference.build_events(path)
    full = inference.predict(events, duration)
    muted = inference.predict(inference.without_audio(events), duration)
    result = extract_response(full, muted, masks)
    width, height = probe_dimensions(path)
    payload = {
        **result,
        "clip": clip.stem,
        "variant": variant,
        "width": width,
        "height": height,
        "seconds_taken": round(time.monotonic() - started, 3),
    }

    result_path = out_dir / f"{clip.stem}.{variant}.json"
    temporary = result_path.with_name(result_path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(result_path)  # a killed run leaves no half-written JSON to skip next time
    print(
        f"  {variant}: {width}x{height}, {duration:.1f} s, {payload['seconds_taken']:.1f} s taken"
    )


def pearson(a, b):
    """Pearson r, or nan when either side has fewer than two points or no variance."""
    if a.size < 2:
        return float("nan")
    da = a - a.mean()
    db = b - b.mean()
    denominator = float(np.sqrt(np.sum(da * da) * np.sum(db * db)))
    if denominator == 0.0:
        return float("nan")
    return float(np.sum(da * db) / denominator)


def compare(crop_result, pad_result):
    """One row per pass and series comparing the crop and pad responses, cut to the shorter
    length (a clip can give a slightly different row count after padding)."""
    rows = []
    for pass_name in PASSES:
        for series in response.SERIES:
            crop = np.asarray(crop_result[pass_name][series], dtype=float)
            pad = np.asarray(pad_result[pass_name][series], dtype=float)
            length = min(crop.size, pad.size)
            crop, pad = crop[:length], pad[:length]
            if length == 0:
                r = mean_crop = mean_pad = mean_abs_diff = float("nan")
            else:
                r = pearson(crop, pad)
                mean_crop = float(crop.mean())
                mean_pad = float(pad.mean())
                mean_abs_diff = float(np.abs(crop - pad).mean())
            rows.append(
                {
                    "pass": pass_name,
                    "series": series,
                    "r": r,
                    "mean_crop": mean_crop,
                    "mean_pad": mean_pad,
                    "mean_abs_diff": mean_abs_diff,
                }
            )
    return rows


def print_table(clip_name, width, height, rows):
    """One readable block per clip, three decimals."""
    print(f"\n{clip_name}  ({width}x{height})")
    print(
        f"  {'pass':<8}{'series':<10}{'r':>8}{'mean_crop':>12}{'mean_pad':>12}{'mean_abs_diff':>16}"
    )
    for row in rows:
        print(
            f"  {row['pass']:<8}{row['series']:<10}{row['r']:>8.3f}"
            f"{row['mean_crop']:>12.3f}{row['mean_pad']:>12.3f}"
            f"{row['mean_abs_diff']:>16.3f}"
        )


def process_clip(clip, out_dir, masks):
    """Run both variants (skipping finished ones) and compare them once both JSONs exist."""
    for variant in VARIANTS:
        result_path = out_dir / f"{clip.stem}.{variant}.json"
        if result_path.exists():
            print(f"  {variant}: skipped, {result_path.name} already exists")
            continue
        path = make_copy(clip, variant, out_dir)
        run_variant(clip, path, variant, out_dir, masks)

    crop_path = out_dir / f"{clip.stem}.crop.json"
    pad_path = out_dir / f"{clip.stem}.pad.json"
    if not (crop_path.exists() and pad_path.exists()):
        print(f"  {clip.name}: no comparison, one variant is missing")
        return []

    crop_result = json.loads(crop_path.read_text())
    pad_result = json.loads(pad_path.read_text())
    rows = compare(crop_result, pad_result)
    width, height = probe_dimensions(clip)  # the source clip's shape, for the table
    print_table(clip.stem, width, height, rows)
    for row in rows:
        row["clip"], row["width"], row["height"] = clip.stem, width, height
    return rows


def write_summary(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Compare crop and letterboxed-pad responses.")
    parser.add_argument("clips", nargs="+", metavar="CLIP", help="video files to compare")
    parser.add_argument("--out", required=True, help="directory for padded copies, JSON and CSV")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    inference.load_model()
    masks = inference.roi_masks()

    rows = []
    failed = []
    for clip in map(Path, args.clips):
        print(f"\n{clip.name}")
        try:
            rows += process_clip(clip, out_dir, masks)
        except Exception as err:  # one bad clip must not stop the others
            print(f"\n!!! {clip}: {type(err).__name__}: {err}", file=sys.stderr)
            failed.append(clip)

    write_summary(out_dir / "summary.csv", rows)
    print(f"\n{len(rows)} comparison rows written to {out_dir / 'summary.csv'}")
    if failed:
        names = ", ".join(clip.name for clip in failed)
        print(f"{len(failed)} clip(s) failed: {names}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
