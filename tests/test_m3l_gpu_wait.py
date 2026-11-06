"""M3l honest GPU wait (docs/M3l_spec.md §2, §7).

A dispatched request records when its GPU wait runs out. Once that time has passed (plus a small
grace), a queued, unclaimed job is refunded ``no_gpu_free`` instead of waiting for the reaper's
four-hour deadline, and the person sees that reason as soon as they look at their jobs. Rows and
money are read directly through PostgreSQL; SageMaker and S3 are fakes.
"""

import json
import threading
import uuid
from datetime import UTC, datetime

import pytest
from conftest import STARTER_CENTS, USER_ID, WEB_USER_ID
from neurolens import billing, storage
from neurolens.db import PostgresDatabase

OTHER_USER = "other-user"
OTHER_EMAIL = f"{OTHER_USER}@example.com"
ENDPOINT = "neurolens-gpu"
MAX_UPLOAD_BYTES = 300_000_000
MAX_S = 120
REAPER_DEADLINE_S = 4 * 60 * 60


def make_job(db, *, user_id=USER_ID, seconds=3.0, starter_cents=STARTER_CENTS):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", starter_cents)
    job_id = str(uuid.uuid4())
    key = storage.object_key(user_id, job_id, "video/mp4")
    billing.reserve(db, user_id, job_id, key, "clip.mp4", round(seconds * 1000))
    return job_id, key


def set_gpu_wait(pg, job_id, seconds_from_now):
    """Move gpu_wait_until that many seconds from the database's now (negative = in the past)."""
    pg.conn.execute(
        "UPDATE jobs SET gpu_wait_until = now() + make_interval(secs => %s) "
        "WHERE job_id = %s::uuid",
        (seconds_from_now, job_id),
    )


def upload(aws, key):
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"an uploaded video")


def assert_refunded_no_gpu_free(pg, job_id, user_id=USER_ID):
    job = pg.job(job_id)
    assert job["status"] == "failed"
    assert job["error_code"] == "no_gpu_free"
    assert pg.refund_reason(job_id) == "no_gpu_free"
    assert pg.balance(user_id) == (STARTER_CENTS, 0)


# ---------------------------------------------------------------- the refund helper


def test_a_queued_unclaimed_job_past_the_grace_is_refunded_no_gpu_free(db, pg):
    job_id, _ = make_job(db)
    set_gpu_wait(pg, job_id, -(billing.GPU_WAIT_GRACE_S + 60))

    assert billing.refund_if_gpu_wait_over(db, job_id) is True
    billing.refund_if_gpu_wait_over(db, job_id)

    assert_refunded_no_gpu_free(pg, job_id)
    assert pg.kinds() == ["starter", "reserve", "refund"]
    assert pg.ledger()[-1]["job_id"] == job_id


def test_a_job_still_inside_the_grace_is_left_alone(db, pg):
    job_id, _ = make_job(db)
    set_gpu_wait(pg, job_id, -(billing.GPU_WAIT_GRACE_S - 60))
    before = pg.snapshot()

    assert billing.refund_if_gpu_wait_over(db, job_id) is False

    assert pg.snapshot() == before


def test_a_claimed_job_is_left_alone_even_if_its_wait_expired(db, pg):
    job_id, _ = make_job(db)
    assert billing.claim(db, job_id) == 1
    set_gpu_wait(pg, job_id, -(billing.GPU_WAIT_GRACE_S + 60))
    before = pg.snapshot()

    assert billing.refund_if_gpu_wait_over(db, job_id) is False

    assert pg.snapshot() == before


def test_a_queued_job_whose_claim_was_already_taken_is_left_alone(db, pg):
    """The claimed_at condition, on its own: a worker claim that has not yet changed the status."""
    job_id, _ = make_job(db)
    set_gpu_wait(pg, job_id, -(billing.GPU_WAIT_GRACE_S + 60))
    pg.conn.execute("UPDATE jobs SET claimed_at = now() WHERE job_id = %s::uuid", (job_id,))
    before = pg.snapshot()

    assert billing.refund_if_gpu_wait_over(db, job_id) is False

    assert pg.snapshot() == before


def test_a_done_job_is_left_alone(db, pg):
    job_id, _ = make_job(db)
    assert billing.settle_success(db, job_id) is True
    set_gpu_wait(pg, job_id, -(billing.GPU_WAIT_GRACE_S + 60))
    before = pg.snapshot()

    assert billing.refund_if_gpu_wait_over(db, job_id) is False

    assert pg.snapshot() == before


def test_a_job_with_no_recorded_wait_is_left_alone(db, pg):
    job_id, _ = make_job(db)
    assert pg.job(job_id)["gpu_wait_until"] is None
    before = pg.snapshot()

    assert billing.refund_if_gpu_wait_over(db, job_id) is False

    assert pg.snapshot() == before


def run_blocked(row_lock, job_id, *calls):
    lock = row_lock()
    lock.execute("SELECT 1 FROM jobs WHERE job_id = %s::uuid FOR UPDATE", job_id)
    results = [None] * len(calls)
    errors = []

    def runner(i, call):
        try:
            results[i] = call()
        except Exception as err:  # reported below
            errors.append(err)

    threads = [threading.Thread(target=runner, args=(i, call)) for i, call in enumerate(calls)]
    for thread in threads:
        thread.start()
    lock.wait_for_blocked(len(calls))
    lock.release()
    for thread in threads:
        thread.join(10)
    assert not any(thread.is_alive() for thread in threads), "a call never finished"
    assert errors == []
    return results


def test_a_claim_and_the_refund_racing_leave_exactly_one_outcome(db, db_dsn, pg, row_lock):
    job_id, _ = make_job(db)
    set_gpu_wait(pg, job_id, -(billing.GPU_WAIT_GRACE_S + 60))

    claimed, refunded = run_blocked(
        row_lock,
        job_id,
        lambda: billing.claim(PostgresDatabase(db_dsn), job_id),
        lambda: billing.refund_if_gpu_wait_over(PostgresDatabase(db_dsn), job_id),
    )

    assert isinstance(refunded, bool)
    assert (claimed == 1) != (refunded is True)
    if refunded:
        assert_refunded_no_gpu_free(pg, job_id)
        assert pg.job(job_id)["attempt"] == 0
    else:
        assert claimed == 1
        assert pg.job(job_id)["status"] == "processing"
        assert pg.kinds().count("refund") == 0


# ---------------------------------------------------------------- the reaper and the error handler


@pytest.fixture
def reaper_env(aws, db, monkeypatch, tmp_path, patch_everywhere):
    monkeypatch.setenv(
        "NEUROLENS_DB_CLUSTER_ARN", "arn:aws:rds:us-east-1:000000000000:cluster:neurolens-db"
    )
    monkeypatch.setenv(
        "NEUROLENS_DB_SECRET_ARN",
        "arn:aws:secretsmanager:us-east-1:000000000000:secret:rds!cluster-test",
    )
    monkeypatch.setenv("NEUROLENS_DB_NAME", "neurolens")
    monkeypatch.setenv("NEUROLENS_S3_BUCKET", aws.bucket)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    (root / "config.json").write_text(json.dumps({"research": {"no_curve_days": 90}}))
    monkeypatch.setenv("NEUROLENS_ROOT", str(root))

    def data_api_database(*args, **kwargs):
        return db

    patch_everywhere(
        "DataApiDatabase", data_api_database, "neurolens.db", "neurolens.lambdas.reaper"
    )


def run_reaper():
    from neurolens.lambdas import reaper

    reaper.handler({}, None)


def test_the_reaper_refunds_an_expired_gpu_wait_as_no_gpu_free(aws, db, pg, reaper_env):
    job_id, key = make_job(db)
    upload(aws, key)
    set_gpu_wait(pg, job_id, -(billing.GPU_WAIT_GRACE_S + 60))
    pg.age(job_id, updated_s=REAPER_DEADLINE_S + 3600, created_s=REAPER_DEADLINE_S + 3600)

    run_reaper()

    assert_refunded_no_gpu_free(pg, job_id)
    assert not storage.object_exists(aws.s3, aws.bucket, key)


def test_the_reaper_keeps_deadline_passed_without_a_recorded_wait(aws, db, pg, reaper_env):
    job_id, key = make_job(db)
    upload(aws, key)
    pg.age(job_id, updated_s=REAPER_DEADLINE_S + 3600, created_s=REAPER_DEADLINE_S + 3600)

    run_reaper()

    job = pg.job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "deadline_passed"
    assert pg.balance() == (STARTER_CENTS, 0)
    assert not storage.object_exists(aws.s3, aws.bucket, key)


def test_finish_job_refunds_an_expired_gpu_wait_as_no_gpu_free(aws, db, pg):
    from neurolens import lambdas

    job_id, key = make_job(db)
    upload(aws, key)
    set_gpu_wait(pg, job_id, -(billing.GPU_WAIT_GRACE_S + 60))

    assert lambdas.finish_job(db, aws.s3, aws.bucket, job_id) is True

    assert_refunded_no_gpu_free(pg, job_id)
    assert not storage.object_exists(aws.s3, aws.bucket, key)


def test_finish_job_keeps_processing_failed_before_the_wait_expires(aws, db, pg):
    from neurolens import lambdas

    job_id, key = make_job(db)
    upload(aws, key)
    set_gpu_wait(pg, job_id, billing.GPU_WAIT_GRACE_S + 60)

    assert lambdas.finish_job(db, aws.s3, aws.bucket, job_id) is True

    job = pg.job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "processing_failed"
    assert pg.balance() == (STARTER_CENTS, 0)
    assert not storage.object_exists(aws.s3, aws.bucket, key)


# ---------------------------------------------------------------- the dispatcher records the wait


class RecordingSageMaker:
    def __init__(self):
        self.calls = []

    def invoke_endpoint_async(self, **kwargs):
        self.calls.append(kwargs)
        return {"InferenceId": kwargs["InferenceId"]}


class FakeAppScaling:
    def describe_scalable_targets(self, **kwargs):
        return {"ScalableTargets": [{"MinCapacity": 0, "MaxCapacity": 1}]}


@pytest.fixture
def probe(monkeypatch):
    from neurolens.lambdas import dispatcher

    def fake(url, timeout_s=30):
        return 3.0

    monkeypatch.setattr(dispatcher, "probe_duration", fake)


def test_the_dispatcher_records_the_wait_it_sent(aws, db, pg, probe):
    from neurolens.lambdas import dispatcher

    job_id, key = make_job(db)
    upload(aws, key)
    sagemaker = RecordingSageMaker()

    outcome = dispatcher.dispatch(
        aws.bucket,
        key,
        s3=aws.s3,
        sagemaker=sagemaker,
        appscaling=FakeAppScaling(),
        db=db,
        endpoint=ENDPOINT,
        max_upload_bytes=MAX_UPLOAD_BYTES,
        max_duration_s=MAX_S,
    )

    assert outcome == "invoked"
    [call] = sagemaker.calls
    row = pg.one(
        "SELECT EXTRACT(EPOCH FROM gpu_wait_until - now()) AS seconds "
        "FROM jobs WHERE job_id = %s::uuid",
        job_id,
    )
    assert row["seconds"] is not None
    assert abs(float(row["seconds"]) - call["RequestTTLSeconds"]) <= 5


# ---------------------------------------------------------------- the web routes


@pytest.fixture
def client(aws, db, make_cfg, tmp_path):
    from neurolens.web.app import create_app

    app = create_app(
        cfg=make_cfg(),
        data_dir=tmp_path / "data",
        db=db,
        s3_client=aws.s3,
    )
    app.config["TESTING"] = True
    return app.test_client()


def test_the_job_list_refunds_the_signed_in_persons_expired_wait(client, db, pg):
    own, _ = make_job(db, user_id=WEB_USER_ID)
    other, _ = make_job(db, user_id=OTHER_USER)
    for job_id in (own, other):
        set_gpu_wait(pg, job_id, -(billing.GPU_WAIT_GRACE_S + 60))

    resp = client.get("/api/jobs")

    assert resp.status_code == 200
    assert_refunded_no_gpu_free(pg, own, WEB_USER_ID)
    assert pg.job(other)["status"] == "queued"
    assert pg.balance(OTHER_USER)[1] > 0


def test_the_status_route_refunds_the_callers_expired_wait(client, db, pg):
    own, _ = make_job(db, user_id=WEB_USER_ID)
    other, _ = make_job(db, user_id=OTHER_USER)
    for job_id in (own, other):
        set_gpu_wait(pg, job_id, -(billing.GPU_WAIT_GRACE_S + 60))

    resp = client.get(f"/api/jobs/{own}/status")
    refused = client.get(f"/api/jobs/{other}/status")

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "failed"
    assert resp.get_json()["error_code"] == "no_gpu_free"
    assert_refunded_no_gpu_free(pg, own, WEB_USER_ID)
    assert refused.status_code == 404
    assert pg.job(other)["status"] == "queued"
    assert pg.balance(OTHER_USER)[1] > 0


def test_the_status_answer_carries_the_recorded_wait(client, db, pg):
    job_id, _ = make_job(db, user_id=WEB_USER_ID)
    set_gpu_wait(pg, job_id, 600)

    resp = client.get(f"/api/jobs/{job_id}/status")

    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data["gpu_wait_until"], str)
    assert data["gpu_wait_until"].endswith(("Z", "+00:00"))
    expected = pg.job(job_id)["gpu_wait_until"].astimezone(UTC)
    assert abs((datetime.fromisoformat(data["gpu_wait_until"]) - expected).total_seconds()) < 2
