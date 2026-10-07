"""Experiment run folders in S3 (docs/M2b_spec.md §10, the artifact contract).

Each run lives in experiments/<experiment>/<run_id>/ with a manifest.json that lists the run's
files. Shared by the experiment scripts and, later, M4's study export. boto3 clients are passed in.
"""

import csv
import io
import json
import subprocess
from pathlib import Path

from botocore.exceptions import ClientError

from neurolens import storage

# The files each experiment must have before its manifest may be written (§10).
EXPERIMENT_FILES = {
    "experiment-1": ["runs.csv"],
    "experiment-2": [
        "jobs.csv",
        "cloudwatch.csv",
        "locust_stats.csv",
        "reliability.csv",
        "cold_start.csv",
    ],
    "experiment-3": ["runs.csv"],
}
# Experiment 1's runs.csv (latency_run writes it, latency_breakdown summarises its *_ms columns).
EXPERIMENT_1_COLUMNS = [
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


def run_prefix(experiment, run_id):
    return f"experiments/{experiment}/{run_id}/"


def code_revision(root=None):
    """The git short hash of the code in use: REVISION on AWS, `git rev-parse` elsewhere."""
    root = Path(root) if root else Path(__file__).resolve().parent.parent
    revision = root / "REVISION"
    if revision.exists():
        return revision.read_text().strip()
    out = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True
    )
    return out.stdout.strip() or "unknown"


def put_text(s3, bucket, experiment, run_id, name, text):
    s3.put_object(
        Bucket=bucket,
        Key=run_prefix(experiment, run_id) + name,
        Body=text.encode(),
        ContentType="text/csv" if name.endswith(".csv") else "application/json",
    )


def get_text(s3, bucket, experiment, run_id, name):
    """The run file's text, or None if there is none."""
    try:
        body = s3.get_object(Bucket=bucket, Key=run_prefix(experiment, run_id) + name)["Body"]
    except ClientError as err:
        if storage.is_not_found(err):
            return None
        raise
    return body.read().decode()


def csv_text(columns, rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: "" if row.get(c) is None else row.get(c) for c in columns})
    return out.getvalue()


def read_csv_rows(text):
    return list(csv.DictReader(io.StringIO(text))) if text else []


def append_row(s3, bucket, experiment, run_id, name, columns, row):
    """Add one row to a run's CSV (read, append, write: one operator at a time is assumed)."""
    rows = read_csv_rows(get_text(s3, bucket, experiment, run_id, name))
    rows.append(row)
    put_text(s3, bucket, experiment, run_id, name, csv_text(columns, rows))


def write_manifest(
    s3,
    bucket,
    experiment,
    run_id,
    *,
    series,
    started_utc,
    finished_utc,
    environment,
    revision=None,
):
    """Write manifest.json. Refuses (ValueError) if any file the experiment needs is missing.
    `revision` is the code the run used; by default the code this runs from (code_revision)."""
    files = EXPERIMENT_FILES[experiment]
    prefix = run_prefix(experiment, run_id)
    missing = [name for name in files if not storage.object_exists(s3, bucket, prefix + name)]
    if missing:
        raise ValueError(f"{experiment}/{run_id} is missing {', '.join(missing)}")
    manifest = {
        "experiment": experiment,
        "run_id": run_id,
        "series": series,
        "started_utc": started_utc,
        "finished_utc": finished_utc,
        "code_revision": revision or code_revision(),
        "environment": environment,
        "files": files,
    }
    put_text(s3, bucket, experiment, run_id, "manifest.json", json.dumps(manifest, indent=2))
    return manifest
