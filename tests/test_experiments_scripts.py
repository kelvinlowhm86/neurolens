"""Experiment scripts (docs/M3e_spec.md §10).

M3e changes where the cold-start rows come from: Application Auto Scaling's scaling activities,
the container's boot records (`container_start_utc`, `model_loaded_utc`, `warmup_done_utc`,
`ready_utc`) and the first job's `transcribing` stage. The CloudWatch export's series become the
endpoint backlog and its instance count, and a run's manifest records the image digest instead
of an AMI id. The unchanged latency scripts keep their pure-function tests.
"""

import importlib.util
import runpy
import statistics
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS = REPO_ROOT / "experiments"

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


def text(name):
    return (EXPERIMENTS / name).read_text()


# ---------------------------------------------------------------- cold_start.py


def test_cold_start_joins_scaling_activities_and_container_boot_records():
    source = text("cold_start.py")
    assert "describe_scaling_activities" in source
    assert "container_start_utc" in source
    assert "model_loaded_utc" in source
    assert "warmup_done_utc" in source
    assert "ready_utc" in source
    assert "describe_auto_scaling_groups" not in source


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
    assert "--instance-type" in proc.stdout


# ---------------------------------------------------------------- export_cloudwatch.py


def test_export_cloudwatch_uses_the_backlog_and_instance_count_series():
    source = text("export_cloudwatch.py")
    assert "ApproximateBacklogSize" in source
    assert "CPUUtilization" in source
    assert "GroupInServiceInstances" not in source
    assert "ApproximateNumberOfMessagesVisible" not in source


# ---------------------------------------------------------------- finish_run.py


def test_finish_run_takes_an_image_digest_not_an_ami_id():
    proc = subprocess.run(
        [sys.executable, str(EXPERIMENTS / "finish_run.py"), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--image-digest" in proc.stdout
    assert "--ami-id" not in proc.stdout


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


# ---------------------------------------------------------------- latency_run (unchanged)

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
    source = text("latency_run.py")
    assert "get_status" not in source  # storage.get_status is removed in M3a
