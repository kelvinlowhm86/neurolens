"""M3a Lambdas: the dead-letter handler and the reaper, called directly (moto S3 + PostgreSQL).
Written first from docs/M3a_spec.md §7 and §10.

On AWS both build a `DataApiDatabase` from environment variables set by Terraform. Here
`neurolens.db.DataApiDatabase` is replaced by a factory returning the test's PostgreSQL database,
so the handlers run unchanged against the real schema; the environment carries the same names as
the worker's deployment values (NEUROLENS_DB_* from §3a, NEUROLENS_S3_BUCKET).
"""

import json
import uuid

import pytest
from conftest import USER_ID
from neurolens import billing, storage

LAMBDA_MODULES = ("neurolens.lambdas.dlq_handler", "neurolens.lambdas.reaper")


@pytest.fixture
def lambda_env(aws, db, monkeypatch, patch_everywhere):
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
    built = []

    def data_api_database(*args, **kwargs):
        built.append((args, kwargs))
        return db

    patch_everywhere("DataApiDatabase", data_api_database, "neurolens.db", *LAMBDA_MODULES)
    return built


def dlq_handler():
    from neurolens.lambdas import dlq_handler

    return dlq_handler.handler


def reaper():
    from neurolens.lambdas import reaper

    return reaper.handler


def sqs_event(*keys, bucket):
    """An SQS event-source event (batch size 1 on AWS) whose body is the S3 notification."""
    body = json.dumps(
        {
            "Records": [
                {"s3": {"bucket": {"name": bucket}, "object": {"key": k, "size": 1}}} for k in keys
            ]
        }
    )
    return {
        "Records": [
            {
                "messageId": str(uuid.uuid4()),
                "receiptHandle": "receipt",
                "body": body,
                "attributes": {"ApproximateReceiveCount": "3"},
                "eventSource": "aws:sqs",
                "eventSourceARN": "arn:aws:sqs:us-east-1:000000000000:neurolens-jobs-dlq",
            }
        ]
    }


def put_result(aws, job_id):
    assert storage.put_result(aws.s3, aws.bucket, job_id, {"job_id": job_id}) is True


def upload(aws, key):
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"an uploaded video")


def failed_twice(db, pg, job_id):
    """A job whose two attempts both failed and were released (status queued, last error)."""
    for attempt in (1, 2):
        assert billing.claim(db, job_id) == attempt
        assert billing.release_for_retry(db, job_id, attempt, f"RuntimeError: crash {attempt}")


# ---------------------------------------------------------------- dead-letter handler


def test_dead_lettered_job_with_a_result_is_settled(aws, db, pg, lambda_env, new_job):
    job_id, key = new_job()
    assert billing.claim(db, job_id) == 1
    put_result(aws, job_id)
    dlq_handler()(sqs_event(key, bucket=aws.bucket), None)
    job = pg.job(job_id)
    assert job["status"] == "done" and job["captured_cents"] == 90
    assert pg.balance() == (410, 0)


def test_dead_lettered_job_without_a_result_is_refunded_with_its_last_error(
    aws, db, pg, lambda_env, new_job
):
    job_id, key = new_job()
    failed_twice(db, pg, job_id)
    dlq_handler()(sqs_event(key, bucket=aws.bucket), None)
    job = pg.job(job_id)
    assert job["status"] == "failed"
    assert job["error_code"] == "processing_failed"
    assert job["error_message"] == "RuntimeError: crash 2"
    assert pg.refund_reason(job_id) == "processing_failed"
    assert pg.balance() == (500, 0)


def test_dead_lettered_job_left_processing_by_a_dead_worker_is_refunded(
    aws, db, pg, lambda_env, new_job
):
    """Two hard crashes (`kill -9`): the job is still `processing`, stale."""
    job_id, key = new_job()
    assert billing.claim(db, job_id) == 1
    pg.age(job_id, updated_s=240)
    dlq_handler()(sqs_event(key, bucket=aws.bucket), None)
    assert pg.job(job_id)["error_code"] == "processing_failed"
    assert pg.balance() == (500, 0)


def test_dead_letter_handler_raises_for_a_freshly_claimed_job(aws, db, pg, lambda_env, new_job):
    """A duplicate message for a job still running: raise, so SQS retries it later."""
    job_id, key = new_job()
    assert billing.claim(db, job_id) == 1
    before = pg.snapshot()
    with pytest.raises(Exception):  # noqa: B017 - the spec says "raise", not which exception
        dlq_handler()(sqs_event(key, bucket=aws.bucket), None)
    assert pg.snapshot() == before


def test_running_the_dead_letter_handler_twice_changes_nothing_more(
    aws, db, pg, lambda_env, new_job
):
    refunded, refunded_key = new_job()
    failed_twice(db, pg, refunded)
    settled, settled_key = new_job()
    put_result(aws, settled)
    for key in (refunded_key, settled_key):
        dlq_handler()(sqs_event(key, bucket=aws.bucket), None)
    before = pg.snapshot()
    for key in (refunded_key, settled_key):
        dlq_handler()(sqs_event(key, bucket=aws.bucket), None)  # returns normally
    assert pg.snapshot() == before


def test_dead_letter_handler_ignores_unknown_and_terminal_jobs(
    aws, db, pg, lambda_env, new_job, new_key
):
    _, unknown_key = new_key()
    done_job, done_key = new_job()
    billing.settle_success(db, done_job)
    before = pg.snapshot()
    event = sqs_event(unknown_key, done_key, bucket=aws.bucket)
    dlq_handler()(event, None)
    assert pg.snapshot() == before


def test_dead_letter_handler_settles_every_job_in_a_multi_record_message(
    aws, db, pg, lambda_env, new_job
):
    a, key_a = new_job()
    b, key_b = new_job()
    put_result(aws, a)
    dlq_handler()(sqs_event(key_a, key_b, bucket=aws.bucket), None)
    assert pg.job(a)["status"] == "done"
    assert pg.job(b)["status"] == "failed"


# ---------------------------------------------------------------- reaper


def run_reaper():
    reaper()({}, None)


def test_reaper_refunds_a_processing_job_with_no_heartbeat_for_10_minutes(
    aws, db, pg, lambda_env, new_job
):
    job_id, key = new_job()
    upload(aws, key)
    assert billing.claim(db, job_id) == 1
    pg.age(job_id, updated_s=660)
    run_reaper()
    job = pg.job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "stalled"
    assert pg.balance() == (500, 0)


def test_reaper_settles_a_stalled_processing_job_whose_result_exists(
    aws, db, pg, lambda_env, new_job
):
    """The crash window: result written, settlement never ran. Charged, not refunded."""
    job_id, _ = new_job()
    assert billing.claim(db, job_id) == 1
    put_result(aws, job_id)
    pg.age(job_id, updated_s=660)
    run_reaper()
    assert pg.job(job_id)["status"] == "done"
    assert pg.balance() == (410, 0)


def test_reaper_leaves_a_processing_job_that_beat_recently(aws, db, pg, lambda_env, new_job):
    job_id, key = new_job()
    upload(aws, key)
    assert billing.claim(db, job_id) == 1
    pg.age(job_id, updated_s=300)
    before = pg.snapshot()
    run_reaper()
    assert pg.snapshot() == before


def test_reaper_refunds_a_queued_job_whose_upload_never_arrived(aws, db, pg, lambda_env, new_job):
    job_id, _ = new_job()  # presigned, never uploaded
    pg.age(job_id, updated_s=3900, created_s=3900)
    run_reaper()
    job = pg.job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "upload_not_received"
    assert pg.balance() == (500, 0)


def test_reaper_settles_an_old_queued_job_whose_result_exists(aws, db, pg, lambda_env, new_job):
    job_id, _ = new_job()
    put_result(aws, job_id)
    pg.age(job_id, updated_s=3900, created_s=3900)
    run_reaper()
    assert pg.job(job_id)["status"] == "done"


def test_reaper_leaves_a_queued_job_2_hours_old_whose_upload_exists(
    aws, db, pg, lambda_env, new_job
):
    """It is waiting for a worker, not stuck: refunding it would leave a paid-back job that
    still runs later."""
    job_id, key = new_job()
    upload(aws, key)
    pg.age(job_id, updated_s=7200, created_s=7200)
    before = pg.snapshot()
    run_reaper()
    assert pg.snapshot() == before


def test_reaper_leaves_a_job_released_5_minutes_ago_though_created_2_hours_ago(
    aws, db, pg, lambda_env, new_job
):
    """Ages run from updated_at, which release_for_retry refreshes. No upload object either, so
    only the age decides."""
    job_id, _ = new_job()
    assert billing.claim(db, job_id) == 1
    assert billing.release_for_retry(db, job_id, 1, "RuntimeError: boom")
    pg.age(job_id, updated_s=300, created_s=7200)
    before = pg.snapshot()
    run_reaper()
    assert pg.snapshot() == before


def test_reaper_leaves_a_queued_job_younger_than_60_minutes_without_an_upload(
    aws, db, pg, lambda_env, new_job
):
    job_id, _ = new_job()
    pg.age(job_id, updated_s=1800, created_s=1800)
    before = pg.snapshot()
    run_reaper()
    assert pg.snapshot() == before


def test_reaper_leaves_terminal_jobs_and_runs_twice_safely(aws, db, pg, lambda_env, new_job):
    stalled, key = new_job()
    upload(aws, key)
    billing.claim(db, stalled)
    pg.age(stalled, updated_s=660)
    done_job, _ = new_job()
    billing.settle_success(db, done_job)
    pg.age(done_job, updated_s=7200)
    run_reaper()
    before = pg.snapshot()
    run_reaper()
    assert pg.snapshot() == before
    assert pg.job(stalled)["status"] == "failed"
    assert pg.job(done_job)["status"] == "done"


def test_reaper_handles_jobs_of_several_users(aws, db, pg, lambda_env, new_job):
    mine, _ = new_job()
    theirs, _ = new_job(user_id="other-user")
    for job_id in (mine, theirs):
        pg.age(job_id, updated_s=3900, created_s=3900)
    run_reaper()
    assert pg.balance(USER_ID) == (500, 0)
    assert pg.balance("other-user") == (500, 0)
