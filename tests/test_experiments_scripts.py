"""Experiment scripts: experiments/cold_start.py `rows` and experiments/latency_breakdown.py
`summarise`, on small hand-made inputs. Written from docs/M2b_spec.md §1a (experiment scripts),
§10 (artifact contract) and §11. experiments/ is not a package, so each script is loaded from its
path.

Input shapes follow boto3's responses: describe_scaling_activities "Activities" entries and
describe_alarm_history "AlarmHistoryItems" entries, with timezone-aware datetimes as boto3 gives
them. In a real launch activity the desired-capacity change that caused it appears only inside
its "Cause" text ("At <time> ... changing the desired capacity from 0 to 1. At <time> an instance
was started ..."), so that is where these inputs carry it.
"""

import importlib.util
import json
import runpy
import statistics
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS = REPO_ROOT / "experiments"

COLD_START_COLUMNS = {
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
}
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


def load(name):
    spec = importlib.util.spec_from_file_location(f"experiment_{name}", EXPERIMENTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def at(hh, mm, ss, day=14):
    return datetime(2026, 10, day, hh, mm, ss, tzinfo=UTC)


def utc_text(value):
    """boot_utc as §4's text, whether the script returns text or a datetime."""
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return value


# ---------------------------------------------------------------- cold_start.rows inputs
# Boot A (day 13, manual): start_work.sh --worker set desired 1 at 05:00:01; launch at 05:00:09.
#   Its log had no weight sync step (None), and it ran no job.
# Boot B (day 14, alarm): scale-out alarm to ALARM at 02:57:30; the policy set desired 1 at
#   02:58:12; the launch (capacity found) started at 03:00:02. First job transcribing 14.2 s.

BOOT_A = {
    "instance_id": "i-0aaaaaaaaaaaaaaaa",
    "instance_type": "t3.large",
    "userdata_start_utc": "2026-10-13T05:00:51Z",
    "weight_sync_start_utc": None,
    "weight_sync_end_utc": None,
    "worker_start_utc": "2026-10-13T05:01:20Z",
    "ready_utc": "2026-10-13T05:02:30Z",
}
BOOT_B = {
    "instance_id": "i-0bbbbbbbbbbbbbbbb",
    "instance_type": "g6e.2xlarge",
    "userdata_start_utc": "2026-10-14T03:00:41Z",
    "weight_sync_start_utc": "2026-10-14T03:00:42Z",
    "weight_sync_end_utc": "2026-10-14T03:01:59Z",
    "worker_start_utc": "2026-10-14T03:02:09Z",
    "ready_utc": "2026-10-14T03:05:58Z",
    "first_job_transcribing_s": 14.2,
}


def activity(instance_id, start, cause, description=None, status="Successful"):
    return {
        "ActivityId": f"act-{instance_id}-{start:%H%M%S}",
        "AutoScalingGroupName": "neurolens-workers",
        "Description": description or f"Launching a new EC2 instance: {instance_id}",
        "Cause": cause,
        "StartTime": start,
        "EndTime": start.replace(second=min(start.second + 30, 59)),
        "StatusCode": status,
        "Progress": 100,
        "Details": json.dumps({"Subnet ID": "subnet-0123", "Availability Zone": "us-east-1a"}),
    }


SCALING_ACTIVITIES = [  # newest first, as AWS returns them
    activity(
        "i-0bbbbbbbbbbbbbbbb",
        at(4, 10, 3),
        "At 2026-10-14T04:10:00Z a monitor alarm neurolens-worker-scale-in in state ALARM "
        "triggered policy neurolens-workers-to-zero changing the desired capacity from 1 to 0.  "
        "At 2026-10-14T04:10:03Z an instance was taken out of service in response to a "
        "difference between desired and actual capacity, shrinking the capacity from 1 to 0.",
        description="Terminating EC2 instance: i-0bbbbbbbbbbbbbbbb",
    ),
    activity(
        "i-0bbbbbbbbbbbbbbbb",
        at(3, 0, 2),
        "At 2026-10-14T02:58:12Z a monitor alarm neurolens-worker-scale-out in state ALARM "
        "triggered policy neurolens-workers-scale-out changing the desired capacity from 0 to "
        "1.  At 2026-10-14T03:00:02Z an instance was started in response to a difference between "
        "desired and actual capacity, increasing the capacity from 0 to 1.",
    ),
    activity(
        "i-0ccccccccccccccccc",  # a launch with no boot record (it never reached the log write)
        at(1, 0, 0),
        "At 2026-10-14T00:59:50Z a user request update of AutoScalingGroup constraints to min: 0, "
        "max: 1, desired: 1 changing the desired capacity from 0 to 1.  At "
        "2026-10-14T01:00:00Z an instance was started in response to a difference between "
        "desired and actual capacity, increasing the capacity from 0 to 1.",
    ),
    activity(
        "i-0aaaaaaaaaaaaaaaa",
        at(5, 0, 9, day=13),
        "At 2026-10-13T05:00:01Z a user request update of AutoScalingGroup constraints to min: 1, "
        "max: 1, desired: 1 changing the desired capacity from 0 to 1.  At "
        "2026-10-13T05:00:09Z an instance was started in response to a difference between "
        "desired and actual capacity, increasing the capacity from 0 to 1.",
    ),
]


def alarm_item(when, old, new):
    return {
        "AlarmName": "neurolens-worker-scale-out",
        "AlarmType": "MetricAlarm",
        "Timestamp": when,
        "HistoryItemType": "StateUpdate",
        "HistorySummary": f"Alarm updated from {old} to {new}",
        "HistoryData": json.dumps(
            {
                "version": "1.0",
                "oldState": {"stateValue": old},
                "newState": {"stateValue": new, "stateReason": "Threshold Crossed"},
            }
        ),
    }


ALARM_HISTORY = [  # newest first
    alarm_item(at(3, 9, 30), "ALARM", "OK"),
    alarm_item(at(2, 57, 30), "OK", "ALARM"),
]


@pytest.fixture
def cold_rows():
    rows = load("cold_start").rows([BOOT_A, BOOT_B], SCALING_ACTIVITIES, ALARM_HISTORY)
    by_type = {r["instance_type"]: r for r in rows}
    return rows, by_type


def test_cold_start_gives_one_row_per_boot_with_exactly_the_contract_columns(cold_rows):
    rows, by_type = cold_rows
    assert len(rows) == 2
    assert set(by_type) == {"t3.large", "g6e.2xlarge"}
    for row in rows:
        assert set(row) == COLD_START_COLUMNS


def test_cold_start_trigger_is_alarm_after_a_scale_out_alarm_and_manual_otherwise(cold_rows):
    _, by_type = cold_rows
    assert by_type["g6e.2xlarge"]["trigger"] == "alarm"
    assert by_type["t3.large"]["trigger"] == "manual"
    assert by_type["t3.large"]["metric_delay_s"] is None  # empty for manual


def test_cold_start_boot_utc_and_capacity_wait_come_from_the_launch_activity(cold_rows):
    _, by_type = cold_rows
    alarm, manual = by_type["g6e.2xlarge"], by_type["t3.large"]
    assert utc_text(alarm["boot_utc"]) == "2026-10-14T03:00:02Z"
    assert utc_text(manual["boot_utc"]) == "2026-10-13T05:00:09Z"
    assert alarm["capacity_wait_s"] == pytest.approx(110)  # 02:58:12 -> 03:00:02
    assert manual["capacity_wait_s"] == pytest.approx(8)  # 05:00:01 -> 05:00:09


def test_cold_start_durations_come_from_the_stamps_and_the_launch_time(cold_rows):
    _, by_type = cold_rows
    b = by_type["g6e.2xlarge"]
    assert b["launch_to_userdata_s"] == pytest.approx(39)  # 03:00:02 -> 03:00:41
    assert b["weight_sync_s"] == pytest.approx(77)  # 03:00:42 -> 03:01:59
    assert b["model_load_s"] == pytest.approx(229)  # 03:02:09 -> 03:05:58
    assert b["ready_s"] == pytest.approx(356)  # 03:00:02 -> 03:05:58
    assert b["first_job_transcribing_s"] == pytest.approx(14.2)


def test_cold_start_missing_values_are_none(cold_rows):
    _, by_type = cold_rows
    a = by_type["t3.large"]
    assert a["weight_sync_s"] is None  # no weight sync step in its log
    assert a["first_job_transcribing_s"] is None  # it ran no job
    assert a["launch_to_userdata_s"] == pytest.approx(42)  # 05:00:09 -> 05:00:51
    assert a["model_load_s"] == pytest.approx(70)  # 05:01:20 -> 05:02:30
    assert a["ready_s"] == pytest.approx(141)  # 05:00:09 -> 05:02:30


# ---------------------------------------------------------------- latency_breakdown.summarise


def run_row(clip, label, base, render=""):
    """One experiment-1/runs.csv row as csv.DictReader gives it (all text)."""
    row = {"clip_seconds": str(clip), "job_label": label, "peak_vram_gb": "21.4"}
    for i, col in enumerate(MS_COLUMNS):
        row[col] = str(base + i)
    row["render_ms"] = render
    return row


RUNS = [
    run_row(15, "J1", 1000, render="120"),
    run_row(15, "J2", 3000, render=""),
    run_row(15, "J3", 2000, render="80"),
    run_row(30, "J4", 5000, render="200"),
    run_row(30, "J5", 7000, render="300"),
]


def stat(row, col, name):
    """§1a names the statistics but not their keys: accept `<col>_<stat>` or row[col][stat],
    with min/max or minimum/maximum."""
    names = {"median": ("median",), "min": ("min", "minimum"), "max": ("max", "maximum")}[name]
    for n in names:
        if f"{col}_{n}" in row:
            return row[f"{col}_{n}"]
        if isinstance(row.get(col), dict) and n in row[col]:
            return row[col][n]
    raise AssertionError(f"no {name} of {col} in {sorted(row)}")


@pytest.fixture
def summary():
    out = load("latency_breakdown").summarise(RUNS)
    return {int(float(r["clip_seconds"])): r for r in out}


def test_summarise_gives_one_entry_per_clip_length_with_its_run_count(summary):
    assert set(summary) == {15, 30}
    assert int(summary[15]["runs"]) == 3
    assert int(summary[30]["runs"]) == 2


def test_summarise_gives_median_min_and_max_of_every_ms_column(summary):
    for col_index, col in enumerate(MS_COLUMNS):
        if col == "render_ms":
            continue
        values = [1000 + col_index, 3000 + col_index, 2000 + col_index]
        assert float(stat(summary[15], col, "median")) == pytest.approx(statistics.median(values))
        assert float(stat(summary[15], col, "min")) == pytest.approx(min(values))
        assert float(stat(summary[15], col, "max")) == pytest.approx(max(values))
    assert float(stat(summary[30], "upload_ms", "median")) == pytest.approx(6000)


def test_summarise_ignores_empty_render_ms(summary):
    assert float(stat(summary[15], "render_ms", "median")) == pytest.approx(100)  # of 120, 80
    assert float(stat(summary[15], "render_ms", "min")) == pytest.approx(80)
    assert float(stat(summary[15], "render_ms", "max")) == pytest.approx(120)


# ---------------------------------------------------------------- import does nothing


@pytest.mark.parametrize("name", ["cold_start.py", "latency_breakdown.py"])
def test_importing_the_script_runs_nothing(name, monkeypatch, capsys):
    """Loaded as a module (not __main__): no command-line parsing, no AWS calls, no output."""
    import boto3

    def no_aws(*a, **kw):
        raise AssertionError("importing an experiment script must not create AWS clients")

    monkeypatch.setattr(boto3, "client", no_aws)
    monkeypatch.setattr(sys, "argv", [name])
    runpy.run_path(str(EXPERIMENTS / name), run_name="experiment_script_under_test")
    assert capsys.readouterr().out == ""


def test_cold_start_command_line_takes_since_and_run_id():
    proc = subprocess.run(
        [sys.executable, str(EXPERIMENTS / "cold_start.py"), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--since" in proc.stdout
    assert "--run-id" in proc.stdout


# ---------------------------------------------------------------- latency_run (M3a §5)
# M3a retires the S3 status objects: latency_run reads `stages` from the status endpoint, whose
# response (docs/M3a_spec.md §6) carries `stages` and `updated_at` in the same format, including
# for a done job. Its stage-duration helper is unchanged.

OLD_STATUS_OBJECT = {
    "job_id": "8a6d2f8e-0000-4000-8000-000000000001",
    "status": "done",
    "stage": "extracting_roi",
    "updated_at": "2026-10-14T03:26:40Z",
    "stages": [
        {"stage": "downloading", "at": "2026-10-14T03:22:10Z"},
        {"stage": "transcribing", "at": "2026-10-14T03:22:12Z"},
        {"stage": "inference_full", "at": "2026-10-14T03:22:41Z"},
        {"stage": "inference_noaudio", "at": "2026-10-14T03:25:02Z"},
        {"stage": "extracting_roi", "at": "2026-10-14T03:26:30Z"},
    ],
    "error": None,
}
STATUS_ENDPOINT_RESPONSE = {
    "job_id": OLD_STATUS_OBJECT["job_id"],
    "status": "done",
    "stage": "extracting_roi",
    "stages": OLD_STATUS_OBJECT["stages"],
    "attempt": 1,
    "updated_at": "2026-10-14T03:26:40Z",
    "error_code": None,
    "error_message": None,
}


def test_latency_run_stage_durations_are_the_same_from_the_status_endpoint():
    latency_run = load("latency_run")
    upload_end = at(3, 22, 4)
    from_endpoint = latency_run.stage_durations_ms(STATUS_ENDPOINT_RESPONSE, upload_end)
    assert from_endpoint == latency_run.stage_durations_ms(OLD_STATUS_OBJECT, upload_end)
    assert from_endpoint == {
        "queue_wait_ms": 6000,
        "downloading_ms": 2000,
        "transcribing_ms": 29000,
        "inference_full_ms": 141000,
        "inference_noaudio_ms": 88000,
        "extracting_roi_ms": 10000,
    }


def test_latency_run_no_longer_reads_the_s3_status_object():
    source = (EXPERIMENTS / "latency_run.py").read_text()
    assert "get_status" not in source  # storage.get_status is removed in M3a
