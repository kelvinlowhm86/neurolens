"""M3c per-video deletion (docs/M3c_spec.md §4, §8).

`DELETE /api/jobs/<job_id>`: every file gone, the job hidden from every route, its ledger intact,
refused while active, safe to repeat, and a reported S3 error still answers success and leaves the
purge to the reaper. Also `billing.delete_job`, `list_jobs` skipping deleted jobs and `job_state`
deliberately not.
"""

import uuid
from pathlib import PurePosixPath

import pytest
from botocore.exceptions import ClientError
from conftest import WEB_USER_ID
from neurolens import billing, storage
from neurolens.web.app import create_app

STARTER_CENTS = 10_000


@pytest.fixture
def app_for(aws, db, make_cfg, tmp_path):
    def make(s3_client=None, **overrides):
        app = create_app(
            cfg=make_cfg(**overrides),
            data_dir=tmp_path / "data",
            db=db,
            s3_client=s3_client or aws.s3,
        )
        app.config["TESTING"] = True
        return app.test_client()

    return make


@pytest.fixture
def client(app_for):
    return app_for()


class NoCallS3:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"s3.{name} must not be called here")

        return call


class DeleteReportsError:
    """A real S3 client whose delete_objects reports an error for every key, as S3 may."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = []

    def delete_objects(self, **kwargs):
        self.calls.append(kwargs)
        objects = kwargs.get("Delete", {}).get("Objects", [])
        return {
            "Errors": [{"Key": o["Key"], "Code": "InternalError"} for o in objects],
            "Deleted": [],
        }

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.fixture(params=["video/mp4", "video/quicktime"], ids=["mp4", "mov"])
def content_type(request):
    return request.param


def make_job(db, *, content_type="video/mp4", done=True, processing=False, user_id=WEB_USER_ID):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", STARTER_CENTS)
    job_id = str(uuid.uuid4())
    key = storage.object_key(user_id, job_id, content_type)
    ext = PurePosixPath(key).suffix
    billing.reserve(db, user_id, job_id, key, f"clip{ext}", 3000)
    if processing:
        assert billing.claim(db, job_id) == 1
    if done:
        assert billing.settle_success(db, job_id) is True
    return job_id, key


def seed_files(aws, job_id, key):
    """Every file §4 step 2 names for this job, with the dataset video's real extension."""
    ext = PurePosixPath(key).suffix
    dataset_video = f"dataset/{job_id}/video{ext}"
    for name, body in (
        (key, b"video"),
        (f"results/{job_id}.json", b"{}"),
        (dataset_video, b"video"),
        (f"dataset/{job_id}/result.json", b"{}"),
        (f"dataset/{job_id}/retention.csv", b"elapsed_ratio,watch_ratio\n0,0\n1,1\n"),
    ):
        aws.s3.put_object(Bucket=aws.bucket, Key=name, Body=body)
    return [
        key,
        f"results/{job_id}.json",
        dataset_video,
        f"dataset/{job_id}/result.json",
        f"dataset/{job_id}/retention.csv",
    ]


def object_exists(aws, key):
    try:
        aws.s3.head_object(Bucket=aws.bucket, Key=key)
    except ClientError as err:
        assert err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound")
        return False
    return True


def history_ids(client):
    return [job["job_id"] for job in client.get("/api/jobs").get_json()]


# ---------------------------------------------------------------- deleting


def test_delete_removes_every_file_marks_the_job_deleted_and_keeps_its_ledger(
    client, aws, db, pg, content_type
):
    job_id, key = make_job(db, content_type=content_type)
    keys = seed_files(aws, job_id, key)
    ledger_before = pg.ledger()
    balance_before = pg.balance()

    resp = client.delete(f"/api/jobs/{job_id}")
    assert resp.status_code in (200, 204)
    assert not any(object_exists(aws, k) for k in keys)
    job = pg.job(job_id)
    assert job is not None
    assert job["deleted_at"] is not None
    assert job["filename"] is None
    assert pg.ledger() == ledger_before
    assert pg.balance() == balance_before


def test_a_deleted_job_is_hidden_from_every_route(client, aws, db, content_type):
    job_id, key = make_job(db, content_type=content_type)
    seed_files(aws, job_id, key)
    assert job_id in history_ids(client)
    assert client.delete(f"/api/jobs/{job_id}").status_code in (200, 204)

    assert job_id not in history_ids(client)
    for route in ("status", "result", "result.csv"):
        resp = client.get(f"/api/jobs/{job_id}/{route}")
        assert resp.status_code == 404, route
        assert resp.get_json()["error"] == "not_found", route


@pytest.mark.parametrize("processing", [False, True], ids=["queued", "processing"])
def test_deleting_an_active_job_is_409_and_deletes_nothing(
    app_for, db, pg, processing, content_type
):
    job_id, _ = make_job(db, content_type=content_type, done=False, processing=processing)
    before = pg.job(job_id)
    s3 = NoCallS3()
    resp = app_for(s3_client=s3).delete(f"/api/jobs/{job_id}")
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "job_active"
    assert s3.calls == []
    after = pg.job(job_id)
    assert after["deleted_at"] is None
    assert after["filename"] == before["filename"]
    assert after["status"] == before["status"]


def test_repeating_delete_is_safe(client, aws, db, pg, content_type):
    job_id, key = make_job(db, content_type=content_type)
    seed_files(aws, job_id, key)
    assert client.delete(f"/api/jobs/{job_id}").status_code in (200, 204)
    deleted_at = pg.job(job_id)["deleted_at"]
    resp = client.delete(f"/api/jobs/{job_id}")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"
    assert pg.job(job_id)["deleted_at"] == deleted_at


def test_delete_with_no_files_still_marks_the_job_deleted(client, db, pg, content_type):
    job_id, _ = make_job(db, content_type=content_type)
    assert client.delete(f"/api/jobs/{job_id}").status_code in (200, 204)
    assert pg.job(job_id)["deleted_at"] is not None


def test_a_reported_s3_delete_error_still_answers_success_and_hides_the_job(
    app_for, aws, db, pg, content_type
):
    job_id, key = make_job(db, content_type=content_type)
    seed_files(aws, job_id, key)
    s3 = DeleteReportsError(aws.s3)
    resp = app_for(s3_client=s3).delete(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.get_json() == {"deleted": True}
    job = pg.job(job_id)
    assert job["deleted_at"] is not None and job["filename"] is None
    assert job["dataset_purged_at"] is None
    assert object_exists(aws, key)
    assert job_id not in history_ids(app_for())


def test_job_status_does_not_expose_the_object_key(client, aws, db, content_type):
    job_id, _ = make_job(db, content_type=content_type)
    resp = client.get(f"/api/jobs/{job_id}/status")
    assert resp.status_code == 200
    assert "object_key" not in resp.get_json()


def test_another_users_job_is_404_with_no_s3_call(app_for, db, content_type):
    job_id, _ = make_job(db, content_type=content_type, user_id="someone-else")
    s3 = NoCallS3()
    resp = app_for(s3_client=s3).delete(f"/api/jobs/{job_id}")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"
    assert s3.calls == []


# ---------------------------------------------------------------- billing.delete_job


def test_billing_delete_job_marks_it_deleted_and_keeps_the_row_and_ledger(db, pg, content_type):
    job_id, _ = make_job(db, content_type=content_type)
    ledger_before = pg.ledger()
    billing.delete_job(db, WEB_USER_ID, job_id)
    job = pg.job(job_id)
    assert job is not None
    assert job["deleted_at"] is not None
    assert job["filename"] is None
    assert job["status"] == "done"
    assert pg.ledger() == ledger_before


def test_billing_delete_job_only_touches_the_owners_job(db, pg, content_type):
    job_id, _ = make_job(db, content_type=content_type)
    filename = pg.job(job_id)["filename"]
    billing.delete_job(db, "someone-else", job_id)
    job = pg.job(job_id)
    assert job["deleted_at"] is None
    assert job["filename"] == filename


def test_list_jobs_skips_deleted_jobs_but_job_state_still_sees_them(db, content_type):
    deleted, _ = make_job(db, content_type=content_type)
    kept, _ = make_job(db, content_type=content_type)
    billing.delete_job(db, WEB_USER_ID, deleted)
    assert {job["job_id"] for job in billing.list_jobs(db, WEB_USER_ID, 50)} == {kept}
    state = billing.job_state(db, deleted)
    assert state is not None and state["status"] == "done"
