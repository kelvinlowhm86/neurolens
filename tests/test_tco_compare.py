"""Experiment 3 cost maths and the benchmark script's import hygiene."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS = REPO_ROOT / "experiments"
HEAVY = ["torch", "tribev2", "nilearn", "huggingface_hub"]


def load(name):
    spec = importlib.util.spec_from_file_location(f"experiment_{name}", EXPERIMENTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tco():
    return load("tco_compare")


def entry(value):
    return {"value": value}


ASSUMPTIONS = {
    "cloud": {
        "gpu_usd_per_hour": entry(3.6),
        "idle_wait_minutes": entry(0),
        "cold_start_minutes": entry(0),
        "videos_per_cold_session": entry(1),
        "fixed_usd_per_month": entry(10),
    },
    "on_prem": {
        "gpu_price_usd": entry(3600),
        "host_price_usd": entry(0),
        "amortisation_months": entry(36),
        "gpu_tdp_watts": entry(1000),
        "host_idle_watts": entry(0),
        "electricity_usd_per_kwh": entry(0.36),
        "hours_on_per_month": entry(720),
    },
}


def cloud_row(clip_seconds, run_seconds):
    return {
        "clip_seconds": str(clip_seconds),
        "transcribing_ms": str(run_seconds * 1000),
        "inference_full_ms": "0",
        "inference_noaudio_ms": "0",
        "extracting_roi_ms": "0",
    }


def onprem_row(video_seconds, total_seconds):
    return {
        "clip": f"clip_{video_seconds}s.mp4",
        "video_seconds": str(video_seconds),
        "run": "1",
        "total_seconds": str(total_seconds),
        "gpu_name": "NVIDIA A100 40GB",
    }


def test_breakeven_value(tco):
    rows, skipped = tco.compare(ASSUMPTIONS, [cloud_row(60, 60)], [onprem_row(60, 60)])

    assert skipped == []
    row = rows[0]
    assert row["cloud_per_video_usd"] == pytest.approx(0.06)
    assert row["onprem_per_video_usd"] == pytest.approx(0.006)
    assert row["cloud_fixed_monthly_usd"] == pytest.approx(10)
    assert row["onprem_fixed_monthly_usd"] == pytest.approx(100)
    assert row["breakeven_videos_per_month"] == pytest.approx(90 / 0.054)
    assert row["onprem_capacity_videos_per_month"] == pytest.approx(720 * 3600 / 60)
    assert row["capacity_warning"] is False


def test_no_breakeven_when_cloud_is_cheaper_per_video(tco):
    assert tco.breakeven_videos(0.05, 0.06, 10, 1000) is None
    assert tco.breakeven_videos(0.05, 0.05, 10, 1000) is None

    cheaper_cloud = {
        "cloud": {**ASSUMPTIONS["cloud"], "gpu_usd_per_hour": entry(0.1)},
        "on_prem": ASSUMPTIONS["on_prem"],
    }
    rows, _ = tco.compare(cheaper_cloud, [cloud_row(60, 60)], [onprem_row(60, 60)])
    assert rows[0]["breakeven_videos_per_month"] is None


def test_capacity_warning_when_breakeven_exceeds_one_gpu(tco):
    assumptions = {
        "cloud": {
            "gpu_usd_per_hour": entry(1.0),
            "idle_wait_minutes": entry(0),
            "cold_start_minutes": entry(0),
            "videos_per_cold_session": entry(1),
            "fixed_usd_per_month": entry(0),
        },
        "on_prem": {
            "gpu_price_usd": entry(36000),
            "host_price_usd": entry(0),
            "amortisation_months": entry(36),
            "gpu_tdp_watts": entry(1000),
            "host_idle_watts": entry(0),
            "electricity_usd_per_kwh": entry(0.5),
            "hours_on_per_month": entry(1),
        },
    }

    rows, _ = tco.compare(assumptions, [cloud_row(60, 60)], [onprem_row(60, 60)])

    assert rows[0]["breakeven_videos_per_month"] == pytest.approx(120_000)
    assert rows[0]["onprem_capacity_videos_per_month"] == pytest.approx(60)
    assert rows[0]["capacity_warning"] is True


def test_length_present_in_only_one_input_is_skipped(tco):
    rows, skipped = tco.compare(
        ASSUMPTIONS,
        [cloud_row(15, 15), cloud_row(30, 30)],
        [onprem_row(15, 15), onprem_row(60, 60)],
    )

    assert [row["clip_seconds"] for row in rows] == [15]
    assert {item["clip_seconds"]: item["missing"] for item in skipped} == {
        30: ["on-prem"],
        60: ["cloud"],
    }


def test_benchmark_import_pulls_in_no_heavy_modules():
    code = (
        "import importlib.util, json, sys; "
        f"spec = importlib.util.spec_from_file_location('tco_benchmark', "
        f"{str(EXPERIMENTS / 'tco_benchmark.py')!r}); "
        "module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
        f"print(json.dumps([name for name in {HEAVY!r} if name in sys.modules]))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == []
