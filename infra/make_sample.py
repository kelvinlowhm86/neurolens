#!/usr/bin/env python3
"""Add or replace one dashboard sample, or a stored comparison, from finished jobs (M3d §9, M3h §7).

    AWS_PROFILE=neurolens python infra/make_sample.py <job_id> <video> <title> <attribution>
    AWS_PROFILE=neurolens python infra/make_sample.py compare <sample_id_1> <job_id_1> \
        <sample_id_2> <job_id_2>

The first form downloads the job's result, brain file and report (when present) into
data/output/<id>/ (id = the video's file stem), grabs a thumbnail at 1 s (or --thumbnail-at), and
writes the entry into data/samples.json with the summary computed from the result. Re-run it
after a summary rule change. The compare form copies the stored comparison for two samples that
are already in samples.json. This is an operator script: it talks to AWS and is never run by the
tests.
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


def _document():
    if SAMPLES.exists():
        return json.loads(SAMPLES.read_text())
    return {"samples": []}


def _write_document(samples, comparisons):
    document = {"samples": samples}
    if comparisons:
        document["comparisons"] = comparisons
    SAMPLES.write_text(json.dumps(document, indent=2) + "\n")


def _object_bytes(s3, bucket, key):
    """The object's body, or None when it does not exist."""
    from botocore.exceptions import ClientError

    from neurolens import storage

    try:
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except FileNotFoundError:
        return None
    except ClientError as err:
        if storage.is_not_found(err):
            return None
        raise


def _without_usage(answer):
    return {key: value for key, value in answer.items() if key != "usage"}


def add_sample(args):
    import boto3

    from neurolens import results
    from neurolens.lambdas import report

    video = video_in_data(args.video)
    sample_id = video.stem
    output = DATA / "output" / sample_id
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "result.json"
    brain_path = output / "brain"
    csv_path = output / "result.csv"
    thumbnail_path = output / "thumb.jpg"

    bucket = bucket_name()
    s3 = boto3.client("s3")
    s3.download_file(bucket, f"results/{args.job_id}.json", str(result_path))
    s3.download_file(bucket, f"results/{args.job_id}.brain", str(brain_path))
    result = json.loads(result_path.read_text())
    if result.get("format") != 2:
        raise SystemExit(f"results/{args.job_id}.json is not format 2: rerun the job first")
    csv_path.write_text(results.to_csv(result), encoding="utf-8")

    stored_report = None
    body = _object_bytes(s3, bucket, f"results/{args.job_id}.report.json")
    if body is not None:
        stored_report = json.loads(body)
        problem = report._report_problem(stored_report, result["duration_seconds"])
        if problem:
            raise SystemExit(f"results/{args.job_id}.report.json is not usable: {problem}")

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
        "csv_file": csv_path.relative_to(DATA).as_posix(),
        "result": result,
        "summary": summary,
    }
    if stored_report is not None:
        entry["report"] = _without_usage(stored_report)

    document = _document()
    samples = [sample for sample in document.get("samples", []) if sample.get("id") != sample_id]
    samples.append(entry)
    _write_document(samples, document.get("comparisons", []))
    print(f"Wrote sample {sample_id} to {SAMPLES} ({len(samples)} samples)")


def add_comparison(args):
    import boto3

    from neurolens import storage
    from neurolens.lambdas import report

    document = _document()
    samples = document.get("samples", [])
    by_id = {sample["id"]: sample for sample in samples}
    missing = [name for name in (args.sample_id_1, args.sample_id_2) if name not in by_id]
    if missing:
        raise SystemExit(f"not in {SAMPLES}: {', '.join(missing)}")

    job_a, job_b = storage.comparison_pair(args.job_id_1, args.job_id_2)
    sample_by_job = {args.job_id_1: args.sample_id_1, args.job_id_2: args.sample_id_2}
    duration_by_job = {
        job: by_id[sample]["result"]["duration_seconds"] for job, sample in sample_by_job.items()
    }

    s3 = boto3.client("s3")
    bucket = bucket_name()
    key = storage.comparison_key(job_a, job_b)
    body = _object_bytes(s3, bucket, key)
    if body is None:
        raise SystemExit(f"{key} does not exist: run the comparison first, then try again")
    answer = json.loads(body)
    problem = report._comparison_problem(answer, duration_by_job[job_a], duration_by_job[job_b])
    if problem:
        raise SystemExit(f"{key} is not usable: {problem}")

    entry = {
        "a": sample_by_job[job_a],
        "b": sample_by_job[job_b],
        "report": _without_usage(answer),
    }
    pair = {entry["a"], entry["b"]}
    comparisons = [
        comparison
        for comparison in document.get("comparisons", [])
        if {comparison.get("a"), comparison.get("b")} != pair
    ]
    comparisons.append(entry)
    _write_document(samples, comparisons)
    print(f"Wrote comparison {entry['a']} / {entry['b']} to {SAMPLES}")


def main():
    values = sys.argv[1:]
    if values and values[0] == "compare":
        parser = argparse.ArgumentParser(
            prog="make_sample.py compare",
            description="Copy a stored comparison between two existing samples.",
        )
        parser.add_argument("sample_id_1")
        parser.add_argument("job_id_1")
        parser.add_argument("sample_id_2")
        parser.add_argument("job_id_2")
        add_comparison(parser.parse_args(values[1:]))
        return

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("video")
    parser.add_argument("title")
    parser.add_argument("attribution")
    parser.add_argument("--description", default=None)
    parser.add_argument(
        "--thumbnail-at", type=float, default=1.0, help="the frame to use, in seconds (default 1)"
    )
    add_sample(parser.parse_args(values))


if __name__ == "__main__":
    main()
