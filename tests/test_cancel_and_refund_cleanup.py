"""M3d's Cancel route and M3a §7b's upload cleanup after a refund.

Written first from docs/M3d_spec.md §7 and §10 ("Cancel route (full: money)") and
docs/M3a_spec.md §7b and §10 ("A refund deletes the upload"). The route is exercised through
Flask's test client on the test PostgreSQL schema and moto S3; the worker is called directly
through `worker.handle_record`, as tests/test_m3a_worker.py does.
"""

import contextlib
import uuid

import pytest
from botocore.exceptions import ClientError
from conftest import STARTER_CENTS, WEB_USER_ID
from neurolens import billing, storage, worker
from neurolens.web.app import create_app


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


def make_job(db, *, state="queued", user_id=WEB_USER_ID, research_consent=False):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", STARTER_CENTS)
    job_id = str(uuid.uuid4())
    key = storage.object_key(user_id, job_id, "video/mp4")
    billing.reserve(db, user_id, job_id, key, "clip.mp4", 3000, research_consent=research_consent)
    if state == "processing":
        assert billing.claim(db, job_id) == 1
    elif state == "done":
        assert billing.settle_success(db, job_id) is True
    elif state == "failed":
        assert billing.issue_refund(db, job_id, "processing_failed", queued_before_s=0) is True
    else:
        assert state == "queued"
    return job_id, key


def cancel(client, job_id):
    return client.post(f"/api/jobs/{job_id}/cancel", json={})


def upload(aws, key):
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"an uploaded video")


def object_exists(aws, key):
    return storage.object_exists(aws.s3, aws.bucket, key)


def dataset_keys(aws, job_id):
    prefix = f"dataset/{job_id}/"
    resp = aws.s3.list_objects_v2(Bucket=aws.bucket, Prefix=prefix)
    return [obj["Key"] for obj in resp.get("Contents", [])]


class DeleteFails:
    """A boto3 S3 client whose delete calls for one key raise, as S3 may."""

    def __init__(self, inner, key):
        self._inner = inner
        self.key = key

    def _error(self, operation):
        raise ClientError(
            {"Error": {"Code": "InternalError", "Message": "delete failed"}}, operation
        )

    def delete_object(self, **kwargs):
        if kwargs.get("Key") == self.key:
            self._error("DeleteObject")
        return self._inner.delete_object(**kwargs)

    def delete_objects(self, **kwargs):
        keys = [obj["Key"] for obj in kwargs.get("Delete", {}).get("Objects", [])]
        if self.key in keys:
            self._error("DeleteObjects")
        return self._inner.delete_objects(**kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class DeleteReportsErrors:
    """A boto3 S3 client whose `delete_objects` reports a per-key error instead of raising."""

    def __init__(self, inner, key):
        self._inner = inner
        self.key = key

    def delete_objects(self, **kwargs):
        objects = kwargs.get("Delete", {}).get("Objects", [])
        if self.key in [obj["Key"] for obj in objects]:
            return {
                "Errors": [{"Key": self.key, "Code": "InternalError", "Message": "delete failed"}],
                "Deleted": [],
            }
        return self._inner.delete_objects(**kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


# ---------------------------------------------------------------- cancel route (money)


def test_cancel_refunds_a_queued_job_once_and_deletes_its_upload(client, aws, db, pg):
    job_id, key = make_job(db)
    upload(aws, key)

    resp = cancel(client, job_id)

    assert resp.status_code == 200
    assert resp.get_json() == {"cancelled": True}
    job = pg.job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "cancelled"
    assert pg.refund_reason(job_id) == "cancelled"
    assert pg.balance(WEB_USER_ID) == (STARTER_CENTS, 0)
    assert pg.kinds(WEB_USER_ID).count("refund") == 1
    assert not object_exists(aws, key)


def test_cancel_deletes_a_research_copy_left_by_an_earlier_attempt(client, aws, db, pg):
    job_id, key = make_job(db, research_consent=True)
    upload(aws, key)
    assert billing.claim(db, job_id) == 1
    storage.put_dataset(aws.s3, aws.bucket, job_id, key, {"job_id": job_id}, brain=b"brain bytes")
    assert billing.release_for_retry(db, job_id, 1, "RuntimeError: crash") is True

    resp = cancel(client, job_id)

    assert resp.status_code == 200
    assert resp.get_json() == {"cancelled": True}
    job = pg.job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "cancelled"
    assert pg.refund_reason(job_id) == "cancelled"
    assert pg.balance(WEB_USER_ID) == (STARTER_CENTS, 0)
    assert not object_exists(aws, key)
    assert dataset_keys(aws, job_id) == []


def test_a_second_cancel_is_409_and_does_not_refund_again(client, aws, db, pg):
    job_id, key = make_job(db)
    upload(aws, key)
    assert cancel(client, job_id).status_code == 200
    before = pg.snapshot()

    resp = cancel(client, job_id)

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "not_cancellable"
    assert pg.snapshot() == before
    assert pg.kinds(WEB_USER_ID).count("refund") == 1


def test_cancel_of_another_users_job_is_404_and_changes_nothing(client, aws, db, pg):
    job_id, key = make_job(db, user_id="someone-else")
    upload(aws, key)
    before = pg.snapshot()

    resp = cancel(client, job_id)

    assert resp.status_code == 404
    assert (resp.get_json() or {}).get("error") == "not_found"
    assert pg.snapshot() == before
    assert object_exists(aws, key)


@pytest.mark.parametrize("state", ["processing", "done", "failed"])
def test_cancel_of_a_started_or_finished_job_is_409_and_changes_nothing(client, aws, db, pg, state):
    job_id, key = make_job(db, state=state)
    upload(aws, key)
    before = pg.snapshot()

    resp = cancel(client, job_id)

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "not_cancellable"
    assert pg.snapshot() == before
    assert object_exists(aws, key)


def test_cancel_after_a_worker_claimed_the_job_is_409_and_leaves_it_processing(client, aws, db, pg):
    job_id, key = make_job(db)
    upload(aws, key)
    assert billing.claim(db, job_id) == 1
    before = pg.snapshot()

    resp = cancel(client, job_id)

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "not_cancellable"
    assert pg.snapshot() == before
    assert pg.job(job_id)["status"] == "processing"
    assert object_exists(aws, key)


def test_a_claim_after_cancel_returns_none(client, aws, db, pg):
    job_id, key = make_job(db)
    upload(aws, key)

    assert cancel(client, job_id).status_code == 200
    assert billing.claim(db, job_id) is None
    assert pg.job(job_id)["status"] == "failed"


def test_cancel_still_answers_200_with_the_credit_back_when_the_upload_delete_fails(
    app_for, aws, db, pg
):
    job_id, key = make_job(db)
    upload(aws, key)

    resp = cancel(app_for(s3_client=DeleteFails(aws.s3, key)), job_id)

    assert resp.status_code == 200
    assert resp.get_json() == {"cancelled": True}
    job = pg.job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "cancelled"
    assert pg.balance(WEB_USER_ID) == (STARTER_CENTS, 0)
    assert pg.kinds(WEB_USER_ID).count("refund") == 1
    assert object_exists(aws, key)


def test_cancel_still_answers_200_with_the_credit_back_when_s3_reports_a_failed_delete(
    app_for, aws, db, pg
):
    job_id, key = make_job(db)
    upload(aws, key)

    resp = cancel(app_for(s3_client=DeleteReportsErrors(aws.s3, key)), job_id)

    assert resp.status_code == 200
    assert resp.get_json() == {"cancelled": True}
    job = pg.job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "cancelled"
    assert pg.balance(WEB_USER_ID) == (STARTER_CENTS, 0)
    assert pg.kinds(WEB_USER_ID).count("refund") == 1
    assert object_exists(aws, key)


# ---------------------------------------------------------------- worker: refund deletes upload


def run_record(aws, db, key, cfg):
    return worker.handle_record(
        aws.bucket,
        key,
        s3=aws.s3,
        db=db,
        cfg=cfg,
        roi_masks=None,
        heartbeat=lambda on_beat: contextlib.nullcontext(),
    )


def test_worker_receiving_an_upload_for_a_refunded_job_deletes_it_and_skips(
    aws, db, pg, make_cfg, new_job
):
    job_id, key = new_job()
    upload(aws, key)
    assert billing.issue_refund(db, job_id, "cancelled", queued_before_s=0) is True
    before = pg.snapshot()

    assert run_record(aws, db, key, make_cfg()) is worker.Outcome.SKIPPED
    assert not object_exists(aws, key)
    assert pg.snapshot() == before


def test_worker_keeps_the_upload_of_a_done_job(aws, db, pg, make_cfg, new_job):
    job_id, key = new_job()
    upload(aws, key)
    assert billing.settle_success(db, job_id) is True
    before = pg.snapshot()

    assert run_record(aws, db, key, make_cfg()) is worker.Outcome.SKIPPED
    assert object_exists(aws, key)
    assert pg.snapshot() == before
