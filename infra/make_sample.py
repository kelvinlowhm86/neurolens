#!/usr/bin/env python3
"""Add or replace one dashboard sample from a finished job (M3d §9).

    AWS_PROFILE=neurolens python infra/make_sample.py <job_id> <video> <title> <attribution>

Downloads the job's result and brain file into data/output/<id>/ (id = the video's file stem),
grabs a thumbnail at 1 s (or --thumbnail-at), and writes the entry into data/samples.json with the
summary computed from the result. Re-run it after a summary rule change. This is an operator
script: it talks to AWS and is never run by the tests.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SAMPLES = DATA / "samples.json"
sys.path.insert(0, str(ROOT))


def bucket_name():
    from_env = os.environ.get("NEUROLENS_S3_BUCKET")
    if from_env:
        return from_env
    return subprocess.check_output(
        ["terraform", "-chdir=infra/terraform", "output", "-raw", "bucket_name"],
        cwd=ROOT,
        text=True,
    ).strip()


def video_in_data(video):
    video = Path(video).resolve()
    if video.is_relative_to(DATA):
        return video
    target = DATA / "videos" / video.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.stat().st_size != video.stat().st_size:
        shutil.copy2(video, target)
    return target


def make_thumbnail(video, target, seconds):
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            str(seconds),
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(target),
        ],
        check=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("video")
    parser.add_argument("title")
    parser.add_argument("attribution")
    parser.add_argument("--description", default=None)
    parser.add_argument(
        "--thumbnail-at", type=float, default=1.0, help="the frame to use, in seconds (default 1)"
    )
    args = parser.parse_args()

    import boto3

    from neurolens import results

    video = video_in_data(args.video)
    sample_id = video.stem
    output = DATA / "output" / sample_id
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "result.json"
    brain_path = output / "brain"
    thumbnail_path = output / "thumb.jpg"

    bucket = bucket_name()
    s3 = boto3.client("s3")
    s3.download_file(bucket, f"results/{args.job_id}.json", str(result_path))
    s3.download_file(bucket, f"results/{args.job_id}.brain", str(brain_path))
    result = json.loads(result_path.read_text())
    if result.get("format") != 2:
        raise SystemExit(f"results/{args.job_id}.json is not format 2: rerun the job first")

    make_thumbnail(video, thumbnail_path, args.thumbnail_at)

    summary = results.summarise(result)
    entry = {
        "id": sample_id,
        "title": args.title,
        "description": args.description if args.description is not None else args.title,
        "attribution": args.attribution,
        "video_file": video.relative_to(DATA).as_posix(),
        "thumbnail": thumbnail_path.relative_to(DATA).as_posix(),
        "brain_file": brain_path.relative_to(DATA).as_posix(),
        "result": result,
        "summary": summary,
    }

    document = json.loads(SAMPLES.read_text()) if SAMPLES.exists() else {"samples": []}
    samples = [sample for sample in document.get("samples", []) if sample.get("id") != sample_id]
    samples.append(entry)
    SAMPLES.write_text(json.dumps({"samples": samples}, indent=2) + "\n")
    print(f"Wrote sample {sample_id} to {SAMPLES} ({len(samples)} samples)")


if __name__ == "__main__":
    main()
