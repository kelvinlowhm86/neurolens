"""Simple no-brain measures for the Ad Meter pilot.

    python experiments/ad_meter_baseline.py CLIP [CLIP ...] --out PATH [--face-model PATH]

Writes one CSV row per clip: clip, duration_seconds, face_share, mean_volume_db,
cuts_per_second. Face share is the share of frames sampled every 0.5 s (OpenCV + YuNet) that
contain at least one face. Volume comes from ffmpeg's volumedetect, cuts from its scene filter.
Every clip is measured first; the CSV is written only when all of them succeeded. Heavy imports
stay inside the functions that use them.
"""

import argparse
import csv
import hashlib
import math
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from neurolens import media

CSV_COLUMNS = ["clip", "duration_seconds", "face_share", "mean_volume_db", "cuts_per_second"]
SAMPLE_SECONDS = 0.5
DEFAULT_FACE_MODEL = Path("data/experiment_results/ad_meter_2026/face_detection_yunet_2023mar.onnx")
FACE_MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
    "face_detection_yunet_2023mar.onnx"
)
FACE_MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
MEAN_VOLUME = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


def parse_mean_volume(stderr_text):
    """The mean volume in dB from ffmpeg's volumedetect output. No line is an error, not zero."""
    match = MEAN_VOLUME.search(stderr_text)
    if match is None:
        raise ValueError("ffmpeg volumedetect gave no mean_volume line")
    return float(match.group(1))


def count_cuts(stderr_text):
    """How many frames the scene filter selected, from ffmpeg showinfo's stderr lines."""
    return sum(1 for line in stderr_text.splitlines() if "pts_time:" in line)


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_face_model(path):
    """Make sure the pinned YuNet model is present, downloading it once if needed."""
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".download")
        try:
            urllib.request.urlretrieve(FACE_MODEL_URL, temporary)
            if sha256_file(temporary) != FACE_MODEL_SHA256:
                raise RuntimeError("the downloaded face model's SHA-256 does not match the pin")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    digest = sha256_file(path)
    if digest != FACE_MODEL_SHA256:
        raise RuntimeError(f"face model {path} has SHA-256 {digest}, expected {FACE_MODEL_SHA256}")


def _run_ffmpeg(command, clip):
    """Run an ffmpeg command, returning stderr; a non-zero exit is an error with its tail."""
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        tail = "\n".join((result.stderr or "").strip().splitlines()[-10:])
        raise RuntimeError(f"ffmpeg failed on {clip} (exit {result.returncode}):\n{tail}")
    return result.stderr or ""


def mean_volume_db(clip):
    stderr = _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(clip),
            "-vn",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        clip,
    )
    return parse_mean_volume(stderr)


def cuts(clip):
    stderr = _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(clip),
            "-an",
            "-vf",
            "select='gt(scene,0.3)',showinfo",
            "-f",
            "null",
            "-",
        ],
        clip,
    )
    return count_cuts(stderr)


def face_share(clip, model_path, duration):
    """Share of frames sampled every 0.5 s of video time in which YuNet finds a face."""
    import cv2

    if duration <= 0:
        raise ValueError(f"{clip}: no video duration to sample")
    capture = cv2.VideoCapture(str(clip))
    try:
        if not capture.isOpened():
            raise RuntimeError(f"{clip}: OpenCV could not open it")
        detector = None
        sampled = 0
        with_face = 0
        for index in range(math.ceil(duration / SAMPLE_SECONDS)):
            if index:
                capture.set(cv2.CAP_PROP_POS_MSEC, index * SAMPLE_SECONDS * 1000)
            ok, frame = capture.read()
            if not ok:
                break
            if detector is None:
                height, width = frame.shape[:2]
                detector = cv2.FaceDetectorYN.create(str(model_path), "", (width, height), 0.8)
                detector.setInputSize((width, height))
            sampled += 1
            _, faces = detector.detect(frame)
            if faces is not None and len(faces) > 0:
                with_face += 1
        if sampled == 0:
            raise RuntimeError(f"{clip}: no readable frames")
        return with_face / sampled
    finally:
        capture.release()


def measure_clip(clip, model_path):
    """All four measures for one clip, rounded for the CSV."""
    duration = media.probe_duration(clip)
    return {
        "clip": clip.stem,
        "duration_seconds": round(duration, 4),
        "face_share": round(face_share(clip, model_path, duration), 4),
        "mean_volume_db": round(mean_volume_db(clip), 4),
        "cuts_per_second": round(cuts(clip) / duration, 4),
    }


def write_csv(path, rows):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description="Simple measures for the Ad Meter pilot.")
    parser.add_argument("clips", nargs="+", metavar="CLIP", help="video files")
    parser.add_argument("--out", required=True, help="CSV path for the measures")
    parser.add_argument(
        "--face-model",
        default=str(DEFAULT_FACE_MODEL),
        help=f"YuNet ONNX model (default {DEFAULT_FACE_MODEL})",
    )
    args = parser.parse_args()

    face_model = Path(args.face_model)
    try:
        ensure_face_model(face_model)
    except Exception as error:  # a missing or wrong model must stop before any clip is measured
        sys.exit(f"Face model unavailable: {error}")

    rows = []
    failed = []
    for clip in map(Path, args.clips):
        try:
            rows.append(measure_clip(clip, face_model))
            print(f"{clip.name}: done")
        except Exception as error:  # one bad clip must not lose the others
            print(f"{clip.name}: FAILED {error}", file=sys.stderr)
            failed.append(clip.name)
    if failed:
        sys.exit(f"Failed: {', '.join(failed)}; no CSV written")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_csv(out, rows)
    print(f"{len(rows)} clips written to {out}")


if __name__ == "__main__":
    main()
