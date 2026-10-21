"""M3d §5 deletion: both brain keys are listed and both deletes remove them (moto).

Mirrors tests/test_m3c_delete_job.py and tests/test_m3c_delete_account.py.
"""

import uuid
from pathlib import PurePosixPath

import pytest
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from conftest import STARTER_CENTS, WEB_USER_ID, sign_in
from neurolens import billing, storage
from neurolens.web.app import _job_keys, create_app


@pytest.fixture
def client(aws, db, make_cfg, tmp_path):
    app = create_app(cfg=make_cfg(), data_dir=tmp_path / "data", db=db, s3_client=aws.s3)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def cognito_admin(monkeypatch):
    """Enough of Cognito's admin API for account deletion: no users to delete."""

    real = BaseClient._make_api_call

    def make_api_call(self, operation_name, api_params):
        if operation_name == "ListUsers":
            return {"Users": []}
        if operation_name == "AdminDeleteUser":
            return {}
        return real(self, operation_name, api_params)

    monkeypatch.setattr(BaseClient, "_make_api_call", make_api_call)


def make_job(db, *, user_id=WEB_USER_ID):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", STARTER_CENTS)
    job_id = str(uuid.uuid4())
    key = storage.object_key(user_id, job_id, "video/mp4")
    ext = PurePosixPath(key).suffix
    billing.reserve(db, user_id, job_id, key, f"clip{ext}", 3000)
    assert billing.claim(db, job_id) == 1
    assert billing.settle_success(db, job_id) is True
    return job_id, key


def seed_files(aws, job_id, key):
    ext = PurePosixPath(key).suffix
    keys = [
        key,
        f"results/{job_id}.json",
        f"results/{job_id}.brain",
        f"dataset/{job_id}/video{ext}",
        f"dataset/{job_id}/result.json",
        f"dataset/{job_id}/brain",
        f"dataset/{job_id}/retention.csv",
    ]
    for name in keys:
        aws.s3.put_object(Bucket=aws.bucket, Key=name, Body=b"x")
    return keys


def object_exists(aws, key):
    try:
        aws.s3.head_object(Bucket=aws.bucket, Key=key)
    except ClientError as err:
        assert err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound")
        return False
    return True


def test_job_keys_lists_both_brain_keys():
    job_id = str(uuid.uuid4())
    job = {"job_id": job_id, "object_key": f"uploads/{WEB_USER_ID}/{job_id}.mp4"}

    keys = _job_keys(job)

    assert f"results/{job_id}.brain" in keys
    assert f"dataset/{job_id}/brain" in keys


def test_per_video_delete_removes_both_brain_files(client, aws, db):
    job_id, key = make_job(db)
    keys = seed_files(aws, job_id, key)

    resp = client.delete(f"/api/jobs/{job_id}")

    assert resp.status_code in (200, 204)
    assert not object_exists(aws, f"results/{job_id}.brain")
    assert not object_exists(aws, f"dataset/{job_id}/brain")
    assert not any(object_exists(aws, name) for name in keys)


def test_account_delete_removes_both_brain_files(cognito_client, idp, aws, db, pg, cognito_admin):
    assert sign_in(cognito_client, idp, email="ann@example.com").status_code in (302, 303)
    user_id = pg.one("SELECT user_id FROM users WHERE lower(email) = 'ann@example.com'")["user_id"]
    job_id, key = make_job(db, user_id=user_id)
    seed_files(aws, job_id, key)

    resp = cognito_client.post("/api/account/delete", json={"confirm": "DELETE"})

    assert resp.status_code == 200
    assert not object_exists(aws, f"results/{job_id}.brain")
    assert not object_exists(aws, f"dataset/{job_id}/brain")
