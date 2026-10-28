"""M2b status and result endpoints, rewritten for M3a (Flask test client + moto + PostgreSQL).
Written from docs/M2b_spec.md §1a (neurolens.web.app routes), §4 and §11, against the
Aurora-backed endpoints of docs/M3a_spec.md §6 with a seeded job owned by the app's development
user. Status and result come from the database alone: a result in S3 never makes an unfinished job
look done. Ownership checks and the refunded-job rules are in tests/test_m3a_web.py.
"""

import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from conftest import STARTER_CENTS, WEB_USER_ID
from neurolens import billing, storage
from neurolens.web.app import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
BAD_IDS = ["abc", "123", "not-a-uuid-at-all", "1234567-1234-1234-1234-123456789abz", "job-1"]


@pytest.fixture
def client(aws, db, make_cfg, tmp_path):
    app = create_app(data_dir=tmp_path / "data", cfg=make_cfg(), db=db, s3_client=aws.s3)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def owned(db):
    """A job reserved for the app's development user, as the presign endpoint leaves it."""

    def make():
        billing.ensure_user(db, WEB_USER_ID, "web@example.com", STARTER_CENTS)
        job_id = str(uuid.uuid4())
        key = storage.object_key(WEB_USER_ID, job_id, "video/mp4")
        billing.reserve(db, WEB_USER_ID, job_id, key, "ad.mp4", 27400)
        return job_id

    return make


def put_json(aws, key, obj):
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=json.dumps(obj).encode())


SERIES = [
    "faces",
    "bodies",
    "scenes",
    "visual",
    "sound",
    "speech",
    "social",
    "see",
    "hear",
    "understand",
    "overall",
]


def format2_result(job_id, **extra):
    return {
        "format": 2,
        "duration_seconds": 1,
        "signals": {name: [0.0] for name in SERIES},
        "muted": {name: [0.0] for name in SERIES},
        "job_id": job_id,
        **extra,
    }


# ---------------------------------------------------------------- status


def test_status_reports_the_db_status_when_a_result_exists_for_a_processing_job(
    client, aws, db, owned
):
    job_id = owned()
    attempt = billing.claim(db, job_id)
    billing.set_stage(db, job_id, attempt, "transcribing")
    put_json(aws, f"results/{job_id}.json", format2_result(job_id))
    resp = client.get(f"/api/jobs/{job_id}/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "processing"
    assert data["job_id"] == job_id


def test_status_reports_the_db_status_when_a_result_exists_for_a_queued_job(client, aws, owned):
    """A result in S3 never makes a queued job look done."""
    job_id = owned()
    put_json(aws, f"results/{job_id}.json", format2_result(job_id))
    resp = client.get(f"/api/jobs/{job_id}/status")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "queued"
    assert resp.get_json()["job_id"] == job_id


def test_status_returns_the_jobs_state_when_there_is_no_result(client, db, owned):
    job_id = owned()
    attempt = billing.claim(db, job_id)
    billing.set_stage(db, job_id, attempt, "downloading")
    billing.set_stage(db, job_id, attempt, "transcribing")
    resp = client.get(f"/api/jobs/{job_id}/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "processing"
    assert data["stage"] == "transcribing"
    assert [s["stage"] for s in data["stages"]] == ["downloading", "transcribing"]
    assert data["error_code"] is None


def test_status_of_a_rejected_job_is_failed_with_its_reason(client, db, owned):
    job_id = owned()
    attempt = billing.claim(db, job_id)
    billing.issue_refund(
        db, job_id, "unreadable_video", "The file is not a readable video.", attempt=attempt
    )
    data = client.get(f"/api/jobs/{job_id}/status").get_json()
    assert data["status"] == "failed"
    assert data["error_code"] == "unreadable_video"
    assert data["error_message"] == "The file is not a readable video."


def test_status_is_404_not_found_for_an_unknown_job(client):
    resp = client.get(f"/api/jobs/{uuid.uuid4()}/status")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"


@pytest.mark.parametrize("bad", BAD_IDS)
def test_status_rejects_a_job_id_that_is_not_a_uuid(client, bad):
    resp = client.get(f"/api/jobs/{bad}/status")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "bad_job_id"


# ---------------------------------------------------------------- result


def test_result_returns_the_stored_json(client, aws, db, owned):
    job_id = owned()
    result = format2_result(job_id, processing_time_seconds=12.3)
    put_json(aws, f"results/{job_id}.json", result)
    billing.settle_success(db, job_id)
    resp = client.get(f"/api/jobs/{job_id}/result")
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data.pop("summary")) == {"audio_dependence"}
    assert data == result


def test_result_is_404_result_not_ready_when_missing_for_a_job_in_progress(client, db, owned):
    job_id = owned()
    attempt = billing.claim(db, job_id)
    billing.set_stage(db, job_id, attempt, "transcribing")
    resp = client.get(f"/api/jobs/{job_id}/result")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "result_not_ready"  # M3b §4a


@pytest.mark.parametrize("bad", BAD_IDS)
def test_result_rejects_a_job_id_that_is_not_a_uuid(client, bad):
    resp = client.get(f"/api/jobs/{bad}/result")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "bad_job_id"


# ---------------------------------------------------------------- import hygiene

ENDPOINTS_CODE = """
import contextlib, json, sys, tempfile, uuid
from pathlib import Path
import boto3
from moto import mock_aws


class EmptyDatabase:
    # The Database interface (M3a 3d) with no rows: every job is unknown.
    @contextlib.contextmanager
    def transaction(self):
        yield self

    def execute(self, sql, params=None):
        return []


with mock_aws():
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="neurolens-hygiene")
    from neurolens.web.app import create_app
    cfg = {
        "paths": {"models": "m", "data": "d"},
        "aws": {"region": "us-east-1", "s3_bucket": "neurolens-hygiene"},
        "max_video_duration_seconds": 120,
        "max_upload_bytes": 300000000,
        "response": {"colour_range": 0.5},
        "auth": {"mode": "dev", "dev_user_id": "dev-user", "dev_email": "dev@localhost"},
        "billing": {"starter_cents": 500},
        "server": {"host": "127.0.0.1", "port": 5003},
    }
    app = create_app(data_dir=Path(tempfile.mkdtemp()), cfg=cfg, db=EmptyDatabase())
    client = app.test_client()
    job_id = str(uuid.uuid4())
    codes = [client.get(f"/api/jobs/{job_id}/status").status_code,
             client.get(f"/api/jobs/{job_id}/result").status_code]
print(json.dumps({"codes": codes,
                  "loaded": [m for m in ("neurolens.inference", "torch") if m in sys.modules]}))
"""


def test_web_app_with_the_new_endpoints_never_loads_inference_or_torch():
    """Runs in a subprocess so sys.modules starts clean (fake AWS credentials are inherited)."""
    proc = subprocess.run(
        [sys.executable, "-c", ENDPOINTS_CODE], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["codes"] == [404, 404]  # the endpoints exist and ran
    assert out["loaded"] == []
