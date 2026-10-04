"""Cold-start table (docs/M2b_spec.md §10): one row per GPU boot, for an experiment-2 run.

Combines what each worker wrote about its own boot (experiments/boots/), the Auto Scaling
group's scaling activities (when AWS launched the machine, and why) and the scale-out alarm's
history. `rows()` is pure; the command line does the AWS calls.

    python experiments/cold_start.py --since 2026-10-14 --run-id 20261014-burst
"""

import argparse
import json
import re
from datetime import UTC, datetime, timedelta

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
COLUMNS = [
    "boot_utc",
    "trigger",
    "instance_type",
    "metric_delay_s",
    "capacity_wait_s",
    "launch_to_userdata_s",
    "weight_sync_s",
    "model_load_s",
    "ready_s",
    "first_job_transcribing_s",
]
SCALE_OUT_ALARM = "neurolens-worker-scale-out"
GROUP = "neurolens-workers"
# An alarm that fired longer than this before the group's capacity change is not its cause.
ALARM_CAUSE_WINDOW = timedelta(minutes=30)
STAMP = re.compile(r"At (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)")


def _parse(text):
    return datetime.strptime(text, TIME_FORMAT).replace(tzinfo=UTC) if text else None


def _text(moment):
    return moment.astimezone(UTC).strftime(TIME_FORMAT) if moment else None


def _seconds(start, end):
    return (end - start).total_seconds() if start and end else None


def launch_activity(instance_id, scaling_activities):
    """The activity that launched this instance, or None."""
    for act in scaling_activities:
        if instance_id in act.get("Description", "") and act["Description"].startswith("Launching"):
            return act
    return None


def desired_change_time(activity):
    """When the desired capacity was changed, from the first "At <time>" of the Cause text (the
    only place AWS records it); the second "At <time>" is the instance start."""
    match = STAMP.search(activity.get("Cause", ""))
    return _parse(match.group(1)) if match else None


def scale_out_alarm_time(activity, alarm_history):
    """When the scale-out alarm went to ALARM for this launch, or None for a manual start.

    The launch counts as alarm-driven only if its cause names an alarm (not a user request) and
    the alarm went to ALARM shortly before the capacity change.
    """
    cause = activity.get("Cause", "")
    changed = desired_change_time(activity)
    if changed is None or "user request" in cause or "alarm" not in cause:
        return None
    best = None
    for item in alarm_history:
        data = json.loads(item.get("HistoryData") or "{}")
        if data.get("newState", {}).get("stateValue") != "ALARM":
            continue
        when = item["Timestamp"].astimezone(UTC)
        if (
            when <= changed
            and changed - when <= ALARM_CAUSE_WINDOW
            and (best is None or when > best)
        ):
            best = when
    return best


def rows(boot_records, scaling_activities, alarm_history):
    """One dict per boot with exactly COLUMNS (missing values None). Pure.

    `metric_delay_s` (upload to scale-out alarm) needs upload times, which are not known here:
    the command line fills it for alarm boots from the run's jobs.csv.
    """
    out = []
    for boot in boot_records:
        act = launch_activity(boot["instance_id"], scaling_activities)
        launched = act["StartTime"].astimezone(UTC) if act else None
        changed = desired_change_time(act) if act else None
        userdata = _parse(boot.get("userdata_start_utc"))
        sync_start = _parse(boot.get("weight_sync_start_utc"))
        sync_end = _parse(boot.get("weight_sync_end_utc"))
        worker_start = _parse(boot.get("worker_start_utc"))
        ready = _parse(boot.get("ready_utc"))
        alarm_driven = bool(act) and scale_out_alarm_time(act, alarm_history) is not None
        out.append(
            {
                "boot_utc": _text(launched),
                "trigger": "alarm" if alarm_driven else "manual",
                "instance_type": boot.get("instance_type"),
                "metric_delay_s": None,
                "capacity_wait_s": _seconds(changed, launched),
                "launch_to_userdata_s": _seconds(launched, userdata),
                "weight_sync_s": _seconds(sync_start, sync_end),
                "model_load_s": _seconds(worker_start, ready),
                "ready_s": _seconds(launched, ready),
                "first_job_transcribing_s": boot.get("first_job_transcribing_s"),
            }
        )
    return out


def _load_boots(s3, bucket, since):
    """Boot records written since `since`, each merged with its first-job record."""
    paginator = s3.get_paginator("list_objects_v2")
    boots, first_jobs = {}, {}
    for page in paginator.paginate(Bucket=bucket, Prefix="experiments/boots/"):
        for obj in page.get("Contents", []):
            if obj["LastModified"] < since:
                continue
            data = json.loads(s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read())
            if obj["Key"].endswith("-first-job.json"):
                first_jobs[data["instance_id"]] = data["first_job_transcribing_s"]
            else:
                boots[data["instance_id"]] = data
    for instance_id, seconds in first_jobs.items():
        if instance_id in boots:
            boots[instance_id]["first_job_transcribing_s"] = seconds
    return list(boots.values())


def _fill_metric_delay(result, boots, activities, alarm_history, jobs):
    """Alarm boots: the alarm's change to ALARM minus the earliest upload submitted before it."""
    submitted = sorted(_parse(j["submitted_utc"]) for j in jobs if j.get("submitted_utc"))
    for row, boot in zip(result, boots, strict=True):
        act = launch_activity(boot["instance_id"], activities)
        if row["trigger"] != "alarm" or not act:
            continue
        alarm_time = scale_out_alarm_time(act, alarm_history)
        earlier = [t for t in submitted if t <= alarm_time]
        if earlier:
            row["metric_delay_s"] = _seconds(min(earlier), alarm_time)


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
    s3 = boto3.client("s3", region_name=aws["region"])
    autoscaling = boto3.client("autoscaling", region_name=aws["region"])
    cloudwatch = boto3.client("cloudwatch", region_name=aws["region"])

    since = datetime.fromisoformat(args.since).replace(tzinfo=UTC)
    boots = _load_boots(s3, bucket, since)
    activities = []
    for page in autoscaling.get_paginator("describe_scaling_activities").paginate(
        AutoScalingGroupName=GROUP
    ):
        activities += page["Activities"]
    history = []
    for page in cloudwatch.get_paginator("describe_alarm_history").paginate(
        AlarmName=SCALE_OUT_ALARM, HistoryItemType="StateUpdate", StartDate=since
    ):
        history += page["AlarmHistoryItems"]

    result = rows(boots, activities, history)
    jobs_text = experiment_runs.get_text(s3, bucket, "experiment-2", args.run_id, "jobs.csv")
    _fill_metric_delay(result, boots, activities, history, experiment_runs.read_csv_rows(jobs_text))
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
