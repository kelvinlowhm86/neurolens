"""Cancel only before dispatch (M3e §8, §11 "Cancel window").

A queued job with no `verified_duration_ms` can be cancelled: refunded and its upload deleted.
Once the dispatcher has priced it (`verified_duration_ms` set), SageMaker will start a GPU for
it, so Cancel answers 409 and changes nothing. `list_jobs` says which is which.
"""

import uuid

import pytest
from conftest import STARTER_CENTS, WEB_USER_ID
from neurolens import billing, storage
from neurolens.web.app import create_app

MAX_S = 120


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


def make_job(db, *, dispatched=False, processing=False, user_id=WEB_USER_ID):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", STARTER_CENTS)
    job_id = str(uuid.uuid4())
    key = storage.object_key(user_id, job_id, "video/mp4")
    billing.reserve(db, user_id, job_id, key, "clip.mp4", 3000)
    if dispatched:
        assert billing.verify_queued(db, job_id, 3000, MAX_S) == "ok"
    if processing:
        assert billing.claim(db, job_id) == 1
    return job_id, key


def cancel(client, job_id):
    return client.post(f"/api/jobs/{job_id}/cancel", json={})


def upload(aws, key):
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"an uploaded video")


# ---------------------------------------------------------------- billing guard


def test_issue_refund_undispatched_only_refuses_a_dispatched_job(db, pg):
    job_id, _ = make_job(db, dispatched=True)
    before = pg.snapshot()

    assert (
        billing.issue_refund(db, job_id, "cancelled", queued=True, undispatched_only=True) is False
    )

    assert pg.snapshot() == before


def test_issue_refund_undispatched_only_still_refunds_an_undispatched_job(db, pg):
    job_id, _ = make_job(db)

    assert (
        billing.issue_refund(db, job_id, "cancelled", queued=True, undispatched_only=True) is True
    )

    assert pg.job(job_id)["status"] == "failed"
    assert pg.refund_reason(job_id) == "cancelled"


# ---------------------------------------------------------------- the route


def test_an_undispatched_queued_job_is_cancelled_and_its_upload_deleted(client, aws, db, pg):
    job_id, key = make_job(db)
    upload(aws, key)

    resp = cancel(client, job_id)

    assert resp.status_code == 200
    assert resp.get_json() == {"cancelled": True}
    assert pg.job(job_id)["status"] == "failed"
    assert pg.balance(WEB_USER_ID) == (STARTER_CENTS, 0)
    assert not storage.object_exists(aws.s3, aws.bucket, key)


def test_a_dispatched_job_is_409_with_the_started_message(client, aws, db, pg):
    job_id, key = make_job(db, dispatched=True)
    upload(aws, key)
    before = pg.snapshot()

    resp = cancel(client, job_id)

    assert resp.status_code == 409
    assert resp.get_json() == {
        "error": "not_cancellable",
        "message": "This analysis has already started.",
    }
    assert pg.snapshot() == before
    assert pg.job(job_id)["status"] == "queued"
    assert storage.object_exists(aws.s3, aws.bucket, key)


def test_list_jobs_cancellable_is_true_only_before_dispatch(db):
    undispatched, _ = make_job(db)
    dispatched, _ = make_job(db, dispatched=True)
    processing, _ = make_job(db, processing=True)

    rows = {
        str(row["job_id"]): row["cancellable"] for row in billing.list_jobs(db, WEB_USER_ID, 50)
    }

    assert rows[undispatched] is True
    assert rows[dispatched] is False
    assert rows[processing] is False


def test_verify_queued_on_a_cancelled_job_changes_nothing(db, pg):
    job_id, _ = make_job(db)
    assert billing.issue_refund(db, job_id, "cancelled", queued=True, undispatched_only=True)
    before = pg.snapshot()

    assert billing.verify_queued(db, job_id, 3000, MAX_S) == "not_queued"

    assert pg.snapshot() == before
    assert pg.job(job_id)["verified_duration_ms"] is None
