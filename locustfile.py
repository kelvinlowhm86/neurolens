"""Experiment 2 load generator (docs/M2b_spec.md §10): each simulated user does one job like a
browser would, then stops, so N users = a burst of N simultaneous jobs.

    BURST_SIZE=5 JOBS_CSV=jobs.csv locust -f locustfile.py --headless -u 5 -r 5 \
        --host http://localhost:5003 --csv locust --run-time 3h

Each user POSTs /api/uploads/presign, uploads the fixed test clip through the presigned POST
(signed fields first, file last), then polls the status until `done` or `failed`. Per-job times go
to JOBS_CSV (columns of experiment-2 jobs.csv); Locust's own --csv files hold the request stats.
Environment: CLIP (default data/videos/_test_clips/clip_15s.mp4), BURST_SIZE, JOBS_CSV.
"""

import csv
import itertools
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from locust import HttpUser, between, task
from locust.exception import StopUser

CLIP = Path(os.environ.get("CLIP", "data/videos/_test_clips/clip_15s.mp4"))
BURST_SIZE = os.environ.get("BURST_SIZE", "")
JOBS_CSV = Path(os.environ.get("JOBS_CSV", "jobs.csv"))
POLL_SECONDS = 5
GIVE_UP_SECONDS = 4 * 60 * 60
COLUMNS = ["burst_size", "job_label", "submitted_utc", "done_utc", "status"]

_labels = itertools.count(1)
_write_lock = threading.Lock()


def _now():
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record(row):
    with _write_lock:
        new = not JOBS_CSV.exists()
        with JOBS_CSV.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
            if new:
                writer.writeheader()
            writer.writerow(row)


class Uploader(HttpUser):
    wait_time = between(0, 0)

    @task
    def one_job(self):
        # Whatever goes wrong, this user ends: a task that raised would otherwise be started again
        # at once, in a tight loop of uploads.
        label = f"J{next(_labels)}"
        submitted = _now()
        try:
            self._run(label, submitted)
        except StopUser:
            raise
        except Exception:
            _record(self._row(label, submitted, "error"))
            raise
        finally:
            self.stop()  # one job per user

    def _run(self, label, submitted):
        body = {
            "filename": CLIP.name,
            "content_type": "video/mp4",
            "client_duration_seconds": 15,
            "client_declared_bytes": CLIP.stat().st_size,
        }
        with self.client.post(
            "/api/uploads/presign", json=body, name="presign", catch_response=True
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"presign HTTP {resp.status_code}")
                _record(self._row(label, submitted, "presign_failed"))
                raise StopUser()
            presign = resp.json()

        with CLIP.open("rb") as f:
            with self.client.post(
                presign["url"],
                data=presign["fields"],
                files={"file": f},
                name="upload to S3",
                catch_response=True,
            ) as resp:
                if resp.status_code != 204:
                    resp.failure(f"upload HTTP {resp.status_code}")
                    _record(self._row(label, submitted, "upload_failed"))
                    raise StopUser()

        status = "timeout"
        deadline = time.monotonic() + GIVE_UP_SECONDS
        while time.monotonic() < deadline:
            time.sleep(POLL_SECONDS)
            with self.client.get(
                f"/api/jobs/{presign['job_id']}/status", name="status", catch_response=True
            ) as resp:
                if resp.status_code == 404:
                    resp.success()  # queued: no worker has taken it yet
                    continue
                state = resp.json().get("status")
                if state in ("done", "failed"):
                    status = state
                    break
        _record(self._row(label, submitted, status))

    @staticmethod
    def _row(label, submitted, status):
        return {
            "burst_size": BURST_SIZE,
            "job_label": label,
            "submitted_utc": submitted,
            "done_utc": _now(),
            "status": status,
        }
