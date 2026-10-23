"""M3e GPU error handler (docs/M3e_spec.md §5, §11 "Error handler").

SageMaker's error topic delivers an SNS notification for any request that crashes, hangs, times
out or expires without a GPU. The handler reuses `lambdas.finish_job`: a result means the job is
settled; otherwise a queued or stale job is refunded `processing_failed` and its files deleted.
A fresh claim is left running and the handler raises so SNS retries later; a terminal job is a
no-op; an unparseable notification is logged and dropped.

Rows and money are read directly through `pg`; the S3 bucket is moto.
"""

import json
import logging

import pytest
from conftest import STARTER_CENTS
from neurolens import billing, lambdas, storage


def gpu_failed():
    from neurolens.lambdas import gpu_failed

    return gpu_failed.handler


@pytest.fixture
def gpu_env(aws, db, monkeypatch):
    """The factory names the handler builds its clients from; the database is PostgreSQL."""
    monkeypatch.setenv("NEUROLENS_S3_BUCKET", aws.bucket)
    monkeypatch.setenv(
        "NEUROLENS_DB_CLUSTER_ARN", "arn:aws:rds:us-east-1:000000000000:cluster:neurolens-db"
    )
    monkeypatch.setenv(
        "NEUROLENS_DB_SECRET_ARN",
        "arn:aws:secretsmanager:us-east-1:000000000000:secret:rds!cluster-test",
    )
    monkeypatch.setenv("NEUROLENS_DB_NAME", "neurolens")
    monkeypatch.setattr(lambdas, "database", lambda: db)
    monkeypatch.setattr(lambdas, "s3_client", lambda: aws.s3)
    return db


def sns_event(job_id):
    message = {
        "awsRegion": "us-east-1",
        "eventTime": "2026-10-22T10:00:00Z",
        "receivedTime": "2026-10-22T10:00:01Z",
        "invocationStatus": "Failed",
        "failureReason": "The request timed out.",
        "requestParameters": {"endpointName": "neurolens-gpu"},
        "inferenceId": job_id,
    }
    return {"Records": [{"Sns": {"Message": json.dumps(message), "MessageId": "m1"}}]}


def upload(aws, key):
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"an uploaded video")


def put_result(aws, job_id):
    assert storage.put_result(aws.s3, aws.bucket, job_id, {"job_id": job_id}) is True


def assert_files_deleted(aws, job_id, key):
    for gone in (key, f"results/{job_id}.json", f"results/{job_id}.brain"):
        assert not storage.object_exists(aws.s3, aws.bucket, gone)


def test_a_job_with_a_result_is_settled(aws, db, pg, gpu_env, new_job):
    job_id, key = new_job()
    upload(aws, key)
    assert billing.claim(db, job_id) == 1
    put_result(aws, job_id)

    gpu_failed()(sns_event(job_id), None)

    job = pg.job(job_id)
    assert job["status"] == "done" and job["captured_cents"] == 90
    assert pg.balance() == (410, 0)
    assert storage.result_exists(aws.s3, aws.bucket, job_id)


def test_a_queued_job_is_refunded_and_its_files_are_deleted(aws, db, pg, gpu_env, new_job):
    job_id, key = new_job()
    upload(aws, key)
    aws.s3.put_object(Bucket=aws.bucket, Key=f"results/{job_id}.brain", Body=b"brain")

    gpu_failed()(sns_event(job_id), None)

    job = pg.job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "processing_failed"
    assert pg.refund_reason(job_id) == "processing_failed"
    assert pg.balance() == (STARTER_CENTS, 0)
    assert_files_deleted(aws, job_id, key)


def test_a_stale_processing_job_is_refunded_and_its_files_are_deleted(
    aws, db, pg, gpu_env, new_job
):
    job_id, key = new_job()
    upload(aws, key)
    assert billing.claim(db, job_id) == 1
    pg.age(job_id, updated_s=240)

    gpu_failed()(sns_event(job_id), None)

    job = pg.job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "processing_failed"
    assert pg.balance() == (STARTER_CENTS, 0)
    assert_files_deleted(aws, job_id, key)


def test_a_fresh_claim_raises_and_changes_nothing(aws, db, pg, gpu_env, new_job):
    job_id, key = new_job()
    upload(aws, key)
    assert billing.claim(db, job_id) == 1
    before = pg.snapshot()
    handler = gpu_failed()

    with pytest.raises(Exception):  # noqa: B017 - the spec says "raises", not which exception
        handler(sns_event(job_id), None)

    assert pg.snapshot() == before
    assert storage.object_exists(aws.s3, aws.bucket, key)


@pytest.mark.parametrize("terminal", ["done", "failed"])
def test_an_already_terminal_job_is_a_noop(aws, db, pg, gpu_env, new_job, terminal):
    job_id, _ = new_job()
    if terminal == "done":
        billing.settle_success(db, job_id)
    else:
        billing.issue_refund(db, job_id, "upload_missing", queued_before_s=0)
    before = pg.snapshot()

    gpu_failed()(sns_event(job_id), None)

    assert pg.snapshot() == before


def test_an_unparseable_notification_is_logged_and_dropped(aws, db, pg, gpu_env, caplog):
    before = pg.snapshot()
    event = {"Records": [{"Sns": {"Message": "this is not json", "MessageId": "m1"}}]}

    with caplog.at_level(logging.DEBUG):
        gpu_failed()(event, None)

    assert caplog.records
    assert pg.snapshot() == before


def test_sagemakers_test_notification_is_ignored_without_a_warning(aws, db, pg, gpu_env, caplog):
    before = pg.snapshot()
    message = json.dumps({"eventName": "TestNotification"})
    event = {"Records": [{"Sns": {"Message": message, "MessageId": "m1"}}]}

    with caplog.at_level(logging.DEBUG):
        gpu_failed()(event, None)

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert pg.snapshot() == before


@pytest.mark.parametrize(
    "message",
    [
        json.dumps([]),
        json.dumps("a JSON string"),
        json.dumps(123),
        json.dumps(None),
    ],
)
def test_a_non_object_notification_is_logged_and_dropped(aws, db, pg, gpu_env, caplog, message):
    before = pg.snapshot()
    event = {"Records": [{"Sns": {"Message": message, "MessageId": "m1"}}]}

    with caplog.at_level(logging.DEBUG):
        gpu_failed()(event, None)

    assert caplog.records
    assert pg.snapshot() == before


@pytest.mark.parametrize(
    "message",
    [
        json.dumps({}),
        json.dumps({"inferenceId": None}),
        json.dumps({"inferenceId": 123}),
    ],
)
def test_a_notification_without_a_string_inference_id_is_logged_and_dropped(
    aws, db, pg, gpu_env, caplog, message
):
    before = pg.snapshot()
    event = {"Records": [{"Sns": {"Message": message, "MessageId": "m1"}}]}

    with caplog.at_level(logging.DEBUG):
        gpu_failed()(event, None)

    assert caplog.records
    assert pg.snapshot() == before
