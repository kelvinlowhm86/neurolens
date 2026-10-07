"""Experiment 1, one run (docs/M2b_spec.md §10): upload one clip like a browser would, follow the
job to the end and add one row to experiment-1/<run_id>/runs.csv in S3.

    python experiments/latency_run.py data/videos/_test_clips/clip_15s.mp4 \
        --api http://localhost:5003 --run-id exp1-20261014 --label J1

Needs the web app running (`python app.py`) and a warm worker (`infra/start_work.sh --worker`,
then one warm-up job). The stage times come from the status endpoint's `stages`, which it keeps
for a finished job (docs/M3a_spec.md §5); the job's end is its `updated_at`. Stage times have
one-second resolution, so the stage columns are whole seconds. `render_ms` is left empty: read
window.neurolensTimings.render_ms in the browser console on 3 runs per clip length and add it by
hand.
"""

import argparse
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

COLUMNS = [
    "clip_seconds",
    "job_label",
    "upload_ms",
    "queue_wait_ms",
    "downloading_ms",
    "transcribing_ms",
    "inference_full_ms",
    "inference_noaudio_ms",
    "extracting_roi_ms",
    "result_fetch_ms",
    "render_ms",
    "peak_vram_gb",
]
STAGES = ["downloading", "transcribing", "inference_full", "inference_noaudio", "extracting_roi"]
CONTENT_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}
POLL_SECONDS = 2
GIVE_UP_SECONDS = 60 * 60


def clip_seconds(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def stage_durations_ms(status, upload_end):
    """Stage times from a finished job's status (the status endpoint's response). A stage lasts
    until the next one starts; the last lasts until the done time (updated_at). queue_wait is the
    first stage's start minus the end of the upload."""
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    starts = {
        s["stage"]: datetime.strptime(s["at"], fmt).replace(tzinfo=UTC) for s in status["stages"]
    }
    done = datetime.strptime(status["updated_at"], fmt).replace(tzinfo=UTC)
    ends = [starts[s] for s in STAGES[1:]] + [done]
    out = {
        "queue_wait_ms": max(0, round((starts["downloading"] - upload_end).total_seconds() * 1000))
    }
    for stage, start, end in zip(STAGES, [starts[s] for s in STAGES], ends, strict=True):
        out[f"{stage}_ms"] = round((end - start).total_seconds() * 1000)
    return out


def main():
    parser = argparse.ArgumentParser(description="One Experiment 1 run.")
    parser.add_argument("clip")
    parser.add_argument("--api", default="http://localhost:5003", help="the web app's address")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--label", required=True, help="short label, J1, J2, ...")
    args = parser.parse_args()

    import boto3
    import requests

    from neurolens import experiment_runs, settings

    cfg = settings.load_settings()
    bucket = cfg["aws"]["s3_bucket"]
    s3 = boto3.client("s3", region_name=cfg["aws"]["region"])
    clip = Path(args.clip)
    seconds = clip_seconds(clip)

    presign = requests.post(
        f"{args.api}/api/uploads/presign",
        json={
            "filename": clip.name,
            "content_type": CONTENT_TYPES[clip.suffix.lower()],
            "client_duration_seconds": seconds,
            "client_declared_bytes": clip.stat().st_size,
        },
        timeout=30,
    )
    presign.raise_for_status()
    presign = presign.json()
    job_id = presign["job_id"]

    t0 = time.monotonic()
    with clip.open("rb") as f:  # signed fields first, the file last
        up = requests.post(presign["url"], data=presign["fields"], files={"file": f}, timeout=600)
    up.raise_for_status()
    upload_ms = round((time.monotonic() - t0) * 1000)
    upload_end = datetime.now(UTC)
    print(f"{args.label}: uploaded {job_id} in {upload_ms} ms")

    deadline = time.monotonic() + GIVE_UP_SECONDS
    while True:
        resp = requests.get(f"{args.api}/api/jobs/{job_id}/status", timeout=30)
        status = resp.json() if resp.status_code == 200 else {}
        if status.get("status") == "done":
            break
        if status.get("status") == "failed":
            raise SystemExit(f"job failed: {status['error_code']}: {status['error_message']}")
        if time.monotonic() > deadline:
            raise SystemExit("gave up waiting for the job")
        time.sleep(POLL_SECONDS)

    t1 = time.monotonic()
    result = requests.get(f"{args.api}/api/jobs/{job_id}/result", timeout=60)
    result.raise_for_status()
    result_fetch_ms = round((time.monotonic() - t1) * 1000)

    row = {
        "clip_seconds": round(seconds),
        "job_label": args.label,
        "upload_ms": upload_ms,
        "result_fetch_ms": result_fetch_ms,
        "render_ms": None,
        "peak_vram_gb": result.json().get("gpu", {}).get("peak_vram_gb"),
        **stage_durations_ms(status, upload_end),
    }
    experiment_runs.append_row(s3, bucket, "experiment-1", args.run_id, "runs.csv", COLUMNS, row)
    print(f"{args.label}: row added to experiments/experiment-1/{args.run_id}/runs.csv")


if __name__ == "__main__":
    main()
