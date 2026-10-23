"""Cold-start table (docs/M3e_spec.md §10): one row per SageMaker scale-out.

Joins Application Auto Scaling's scaling activities with the container's own boot record
(`container_start_utc`, `model_loaded_utc`, `warmup_done_utc`, `ready_utc`) and the first job's
`transcribing` start. `rows()` is pure; the command line does the AWS calls.

    python experiments/cold_start.py --since 2026-10-14 --run-id 20261014-burst
"""

import argparse
import json
from datetime import UTC, datetime

from neurolens.storage import utc_parse, utc_text

COLUMNS = [
    "boot_utc",
    "trigger",
    "instance_type",
    "capacity_wait_s",
    "container_start_utc",
    "model_loaded_utc",
    "warmup_done_utc",
    "ready_utc",
    "model_load_s",
    "warmup_s",
    "startup_s",
    "first_job_transcribing_s",
]
VARIANT = "main"


def _parse(text):
    return utc_parse(text) if text else None


def _text(moment):
    return utc_text(moment) if moment else None


def _seconds(start, end):
    return (end - start).total_seconds() if start and end else None


def launch_activity(container_start, scaling_activities):
    """The latest scale-out activity that started before this container, or None."""
    candidates = []
    for act in scaling_activities:
        start = act.get("StartTime")
        if start is None:
            continue
        start = start.astimezone(UTC)
        if start <= container_start:
            candidates.append((start, act))
    return max(candidates, default=(None, None), key=lambda pair: pair[0])[1]


def rows(boot_records, scaling_activities):
    """One dict per boot with exactly COLUMNS (missing values None). Pure."""
    out = []
    for boot in boot_records:
        container = _parse(boot.get("container_start_utc"))
        model_loaded = _parse(boot.get("model_loaded_utc"))
        warmup_done = _parse(boot.get("warmup_done_utc"))
        ready = _parse(boot.get("ready_utc"))
        act = launch_activity(container, scaling_activities) if container else None
        launched = act["StartTime"].astimezone(UTC) if act else None
        cause = (act or {}).get("Cause", "")
        out.append(
            {
                "boot_utc": _text(launched or container),
                "trigger": "alarm" if "alarm" in cause else "manual",
                "instance_type": boot.get("instance_type"),
                "capacity_wait_s": _seconds(launched, container),
                "container_start_utc": boot.get("container_start_utc"),
                "model_loaded_utc": boot.get("model_loaded_utc"),
                "warmup_done_utc": boot.get("warmup_done_utc"),
                "ready_utc": boot.get("ready_utc"),
                "model_load_s": _seconds(container, model_loaded),
                "warmup_s": _seconds(model_loaded, warmup_done),
                "startup_s": _seconds(container, ready),
                "first_job_transcribing_s": boot.get("first_job_transcribing_s"),
            }
        )
    return out


def _load_boots(s3, bucket, since):
    paginator = s3.get_paginator("list_objects_v2")
    boots = []
    for page in paginator.paginate(Bucket=bucket, Prefix="experiments/boots/"):
        for obj in page.get("Contents", []):
            if obj["LastModified"] < since:
                continue
            data = json.loads(s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read())
            if "container_start_utc" in data:
                boots.append(data)
    return boots


def _first_transcribing_s(boot, jobs):
    ready = _parse(boot.get("ready_utc"))
    if ready is None:
        return None
    starts = []
    for job in jobs:
        text = job.get("transcribing_utc") or job.get("transcribing_start_utc")
        moment = _parse(text) if text else None
        if moment is not None and moment >= ready:
            starts.append(moment)
    return _seconds(ready, min(starts)) if starts else None


def main():
    parser = argparse.ArgumentParser(description="Build cold_start.csv for an experiment-2 run.")
    parser.add_argument("--since", required=True, help="first day to include, e.g. 2026-10-14")
    parser.add_argument("--run-id", required=True, help="the experiment-2 run to write into")
    args = parser.parse_args()

    import boto3

    from neurolens import experiment_runs, settings

    cfg = settings.load_settings()
    aws = cfg["aws"]
    bucket = aws["s3_bucket"]
    endpoint = aws["gpu_endpoint"]
    s3 = boto3.client("s3", region_name=aws["region"])
    appscaling = boto3.client("application-autoscaling", region_name=aws["region"])

    since = datetime.fromisoformat(args.since).replace(tzinfo=UTC)
    boots = _load_boots(s3, bucket, since)
    activities = []
    for page in appscaling.get_paginator("describe_scaling_activities").paginate(
        ServiceNamespace="sagemaker",
        ResourceId=f"endpoint/{endpoint}/variant/{VARIANT}",
        ScalableDimension="sagemaker:variant:DesiredInstanceCount",
    ):
        activities += page["ScalingActivities"]

    jobs = experiment_runs.read_csv_rows(
        experiment_runs.get_text(s3, bucket, "experiment-2", args.run_id, "jobs.csv")
    )
    for boot in boots:
        boot["first_job_transcribing_s"] = _first_transcribing_s(boot, jobs)

    result = rows(boots, activities)
    experiment_runs.put_text(
        s3,
        bucket,
        "experiment-2",
        args.run_id,
        "cold_start.csv",
        experiment_runs.csv_text(COLUMNS, result),
    )
    print(f"{len(result)} boots written to experiments/experiment-2/{args.run_id}/cold_start.csv")


if __name__ == "__main__":
    main()
