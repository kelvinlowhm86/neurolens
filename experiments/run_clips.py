"""Brain response for local clips, the same way the worker makes it (no AWS).

    python experiments/run_clips.py CLIP [CLIP ...] --out DIR [--save-predictions]

Each clip gives DIR/<clip stem>.json: the response plus the spoken words the report uses. A finished
clip is skipped on a rerun. With --save-predictions each clip also gives DIR/<clip stem>.npy, the
with-sound prediction for every point of the brain surface (seconds x 20,484, float32), so other
region groupings can be tried later without the GPU. FAKE_INFERENCE=1 works without a GPU or
model weights.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from neurolens import inference, media
from neurolens.response import extract_response


def run_clip(clip, out_dir, masks, save_predictions=False):
    """Predict one clip and write its JSON. Nothing is written before the prediction finishes, and
    the JSON (which marks the clip as done) is written last."""
    duration = media.probe_duration(clip)
    events = inference.build_events(clip)
    full = inference.predict(events, duration)
    muted = inference.predict(inference.without_audio(events), duration)
    payload = {
        **extract_response(full, muted, masks),
        "words": inference.spoken_words(events),
    }
    if save_predictions:
        raw = out_dir / f"{clip.stem}.npy"
        temporary = raw.with_name(raw.name + ".tmp")
        with temporary.open("wb") as handle:
            np.save(handle, np.asarray(full, dtype=np.float32))
        temporary.replace(raw)
    path = out_dir / f"{clip.stem}.json"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description="Brain response for local clips.")
    parser.add_argument("clips", nargs="+", metavar="CLIP", help="video files")
    parser.add_argument("--out", required=True, help="directory for the JSON results")
    parser.add_argument(
        "--save-predictions",
        action="store_true",
        help="also save each clip's with-sound prediction for the whole brain surface (.npy)",
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    inference.load_model()
    masks = inference.roi_masks()

    failed = []
    for clip in map(Path, args.clips):
        if (out_dir / f"{clip.stem}.json").exists():
            print(f"{clip.name}: done already, skipped")
            continue
        try:
            run_clip(clip, out_dir, masks, args.save_predictions)
            print(f"{clip.name}: done")
        except Exception as error:  # one bad clip must not lose the others
            print(f"{clip.name}: FAILED {error!r}", file=sys.stderr)
            failed.append(clip.name)
    if failed:
        sys.exit(f"Failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
