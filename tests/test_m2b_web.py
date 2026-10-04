"""M2b status and result endpoints (Flask test client + moto). Written from docs/M2b_spec.md §1a
(neurolens.web.app routes), §4 and §11."""

import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from neurolens.web.app import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
BAD_IDS = ["abc", "123", "not-a-uuid-at-all", "1234567-1234-1234-1234-123456789abz", "job-1"]


@pytest.fixture
def client(make_cfg, tmp_path):
    app = create_app(data_dir=tmp_path / "data", cfg=make_cfg())
    app.config["TESTING"] = True
    return app.test_client()


def put_json(aws, key, obj):
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=json.dumps(obj).encode())


def status_obj(job_id, status="processing", stage="transcribing"):
    return {
        "job_id": job_id,
        "status": status,
        "stage": stage,
        "updated_at": "2026-10-14T03:22:41Z",
        "stages": [
            {"stage": "downloading", "at": "2026-10-14T03:22:10Z"},
            {"stage": "transcribing", "at": "2026-10-14T03:22:41Z"},
        ],
        "error": None,
    }


# ---------------------------------------------------------------- status


def test_status_is_done_when_a_result_exists_even_if_the_status_object_says_processing(client, aws):
    job_id = str(uuid.uuid4())
    put_json(aws, f"status/{job_id}.json", status_obj(job_id))
    put_json(aws, f"results/{job_id}.json", {"job_id": job_id, "timesteps": []})
    resp = client.get(f"/api/jobs/{job_id}/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "done"
    assert data["job_id"] == job_id


def test_status_is_done_when_a_result_exists_and_there_is_no_status_object(client, aws):
    """A crash after the result write but before the done status still reports done."""
    job_id = str(uuid.uuid4())
    put_json(aws, f"results/{job_id}.json", {"job_id": job_id})
    resp = client.get(f"/api/jobs/{job_id}/status")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "done"
    assert resp.get_json()["job_id"] == job_id


@pytest.mark.parametrize(
    "status, stage, error",
    [
        ("processing", "transcribing", None),
        ("processing", "retrying", "The model crashed."),
        ("failed", None, "The file is not a readable video."),
    ],
)
def test_status_returns_the_status_object_when_there_is_no_result(
    client, aws, status, stage, error
):
    job_id = str(uuid.uuid4())
    obj = {**status_obj(job_id, status, stage), "error": error}
    put_json(aws, f"status/{job_id}.json", obj)
    resp = client.get(f"/api/jobs/{job_id}/status")
    assert resp.status_code == 200
    assert resp.get_json() == obj


def test_status_is_404_not_found_when_neither_exists(client):
    resp = client.get(f"/api/jobs/{uuid.uuid4()}/status")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"


@pytest.mark.parametrize("bad", BAD_IDS)
def test_status_rejects_a_job_id_that_is_not_a_uuid(client, bad):
    resp = client.get(f"/api/jobs/{bad}/status")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "bad_job_id"


# ---------------------------------------------------------------- result


def test_result_returns_the_stored_json(client, aws):
    job_id = str(uuid.uuid4())
    result = {
        "job_id": job_id,
        "duration_seconds": 3,
        "timesteps": [{"t": 0, "engagement_overall": 0.5}],
        "processing_time_seconds": 12.3,
    }
    put_json(aws, f"results/{job_id}.json", result)
    resp = client.get(f"/api/jobs/{job_id}/result")
    assert resp.status_code == 200
    assert resp.get_json() == result


def test_result_is_404_not_found_when_missing_even_with_a_status_object(client, aws):
    job_id = str(uuid.uuid4())
    put_json(aws, f"status/{job_id}.json", status_obj(job_id))
    resp = client.get(f"/api/jobs/{job_id}/result")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"


@pytest.mark.parametrize("bad", BAD_IDS)
def test_result_rejects_a_job_id_that_is_not_a_uuid(client, bad):
    resp = client.get(f"/api/jobs/{bad}/result")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "bad_job_id"


# ---------------------------------------------------------------- import hygiene

ENDPOINTS_CODE = """
import json, sys, tempfile, uuid
from pathlib import Path
import boto3
from moto import mock_aws
with mock_aws():
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="neurolens-hygiene")
    from neurolens.web.app import create_app
    cfg = {
        "paths": {"models": "m", "data": "d", "output": "o"},
        "aws": {"region": "us-east-1", "s3_bucket": "neurolens-hygiene",
                "sqs_queue_url": "https://sqs.us-east-1.amazonaws.com/123456789012/q"},
        "max_video_duration_seconds": 120,
        "max_upload_bytes": 300000000,
    }
    app = create_app(data_dir=Path(tempfile.mkdtemp()), cfg=cfg)
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
