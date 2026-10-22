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
from botocore.exceptions import ClientError
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


def test_a_dead_letter_refund_deletes_the_jobs_upload(aws, db, pg, lambda_env, new_job):
    job_id, key = new_job()
    failed_twice(db, pg, job_id)
    upload(aws, key)
    dlq_handler()(sqs_event(key, bucket=aws.bucket), None)
    assert pg.job(job_id)["status"] == "failed"
    assert pg.refund_reason(job_id) == "processing_failed"
    assert not storage.object_exists(aws.s3, aws.bucket, key)


# ---------------------------------------------------------------- reaper

DEADLINE_S = 24 * 60 * 60
HEARTBEAT_STALE_S = 10 * 60


def run_reaper():
    reaper()({}, None)


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


def test_reaper_refunds_a_queued_job_past_the_24_hour_deadline_and_deletes_its_upload(
    aws, db, pg, lambda_env, new_job
):
    job_id, key = new_job()
    upload(aws, key)
    pg.age(job_id, updated_s=DEADLINE_S + 3600, created_s=DEADLINE_S + 3600)
    run_reaper()
    job = pg.job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "deadline_passed"
    assert pg.refund_reason(job_id) == "deadline_passed"
    assert pg.balance() == (500, 0)
    assert not storage.object_exists(aws.s3, aws.bucket, key)


def test_reaper_refunds_a_queued_job_past_the_deadline_even_without_an_upload(
    aws, db, pg, lambda_env, new_job
):
    job_id, _ = new_job()  # presigned, never uploaded
    pg.age(job_id, updated_s=DEADLINE_S + 3600, created_s=DEADLINE_S + 3600)
    run_reaper()
    job = pg.job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "deadline_passed"
    assert pg.refund_reason(job_id) == "deadline_passed"
    assert pg.balance() == (500, 0)


def test_reaper_leaves_a_queued_job_younger_than_24_hours_even_without_an_upload(
    aws, db, pg, lambda_env, new_job
):
    job_id, _ = new_job()
    pg.age(job_id, updated_s=DEADLINE_S - 3600, created_s=DEADLINE_S - 3600)
    before = pg.snapshot()
    run_reaper()
    assert pg.snapshot() == before


def test_reaper_settles_a_job_past_the_deadline_whose_result_exists_and_keeps_the_upload(
    aws, db, pg, lambda_env, new_job
):
    job_id, key = new_job()
    upload(aws, key)
    put_result(aws, job_id)
    pg.age(job_id, updated_s=DEADLINE_S + 3600, created_s=DEADLINE_S + 3600)
    run_reaper()
    job = pg.job(job_id)
    assert job["status"] == "done" and job["captured_cents"] == 90
    assert pg.balance() == (410, 0)
    assert storage.object_exists(aws.s3, aws.bucket, key)


def test_reaper_leaves_a_processing_job_past_the_deadline_that_beat_a_minute_ago(
    aws, db, pg, lambda_env, new_job
):
    job_id, key = new_job()
    upload(aws, key)
    assert billing.claim(db, job_id) == 1
    pg.age(job_id, updated_s=60, created_s=DEADLINE_S + 3600)
    before = pg.snapshot()
    run_reaper()
    assert pg.snapshot() == before
    assert storage.object_exists(aws.s3, aws.bucket, key)


def test_reaper_refunds_a_processing_job_past_the_deadline_with_no_heartbeat_for_11_minutes(
    aws, db, pg, lambda_env, new_job
):
    job_id, key = new_job()
    upload(aws, key)
    assert billing.claim(db, job_id) == 1
    pg.age(job_id, updated_s=HEARTBEAT_STALE_S + 60, created_s=DEADLINE_S + 3600)
    run_reaper()
    job = pg.job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "deadline_passed"
    assert pg.balance() == (500, 0)
    assert not storage.object_exists(aws.s3, aws.bucket, key)


def test_reaper_leaves_a_processing_job_under_24_hours_old_although_its_heartbeat_stopped(
    aws, db, pg, lambda_env, new_job
):
    """The retry path owns a recent crash; the reaper is only the backstop."""
    job_id, key = new_job()
    upload(aws, key)
    assert billing.claim(db, job_id) == 1
    pg.age(job_id, updated_s=HEARTBEAT_STALE_S + 60, created_s=2 * 3600)
    before = pg.snapshot()
    run_reaper()
    assert pg.snapshot() == before
    assert storage.object_exists(aws.s3, aws.bucket, key)


def test_reaper_leaves_a_job_claimed_between_its_query_and_the_refund(
    aws, db, pg, lambda_env, new_job, patch_everywhere
):
    job_id, key = new_job()
    upload(aws, key)
    pg.age(job_id, updated_s=DEADLINE_S + 3600, created_s=DEADLINE_S + 3600)
    real_issue_refund = billing.issue_refund
    claimed = []

    def refund_after_a_worker_claimed(*args, **kwargs):
        if not claimed:
            claimed.append(job_id)
            assert billing.claim(db, job_id) == 1
        return real_issue_refund(*args, **kwargs)

    patch_everywhere(
        "issue_refund",
        refund_after_a_worker_claimed,
        "neurolens.billing",
        "neurolens.lambdas.reaper",
    )
    run_reaper()
    assert claimed == [job_id]
    assert pg.job(job_id)["status"] == "processing"
    assert pg.balance() == (410, 90)
    assert storage.object_exists(aws.s3, aws.bucket, key)


def test_reaper_refunds_a_job_whose_upload_delete_fails_and_still_reaps_the_others(
    aws, db, pg, lambda_env, new_job, monkeypatch
):
    first, first_key = new_job()
    second, second_key = new_job()
    upload(aws, first_key)
    upload(aws, second_key)
    for job_id in (first, second):
        pg.age(job_id, updated_s=DEADLINE_S + 3600, created_s=DEADLINE_S + 3600)

    import boto3

    real_client = boto3.client

    def client(service_name, *args, **kwargs):
        inner = real_client(service_name, *args, **kwargs)
        return DeleteFails(inner, first_key) if service_name == "s3" else inner

    monkeypatch.setattr(boto3, "client", client)
    with pytest.raises(Exception):  # noqa: B017 - the spec says the run raises
        run_reaper()

    for job_id in (first, second):
        job = pg.job(job_id)
        assert job["status"] == "failed" and job["error_code"] == "deadline_passed"
        assert pg.refund_reason(job_id) == "deadline_passed"
    assert pg.balance() == (500, 0)
    assert not storage.object_exists(aws.s3, aws.bucket, second_key)
    assert storage.object_exists(aws.s3, aws.bucket, first_key)


def test_reaper_leaves_terminal_jobs_and_runs_twice_safely(aws, db, pg, lambda_env, new_job):
    failed, _ = new_job()
    billing.issue_refund(db, failed, "cancelled", queued_before_s=0)
    pg.age(failed, updated_s=DEADLINE_S + 3600, created_s=DEADLINE_S + 3600)
    done_job, _ = new_job()
    billing.settle_success(db, done_job)
    pg.age(done_job, updated_s=DEADLINE_S + 3600, created_s=DEADLINE_S + 3600)
    run_reaper()
    before = pg.snapshot()
    run_reaper()
    assert pg.snapshot() == before
    assert pg.job(failed)["status"] == "failed"
    assert pg.job(done_job)["status"] == "done"


def test_reaper_handles_jobs_of_several_users(aws, db, pg, lambda_env, new_job):
    mine, _ = new_job()
    theirs, _ = new_job(user_id="other-user")
    for job_id in (mine, theirs):
        pg.age(job_id, updated_s=DEADLINE_S + 3600, created_s=DEADLINE_S + 3600)
    run_reaper()
    assert pg.balance(USER_ID) == (500, 0)
    assert pg.balance("other-user") == (500, 0)
