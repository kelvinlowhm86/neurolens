"""M3e dispatcher (docs/M3e_spec.md §2, §3, §11 "Dispatcher").

The dispatcher sits between S3's upload notice and the async GPU endpoint: it checks the file,
prices it through `billing.verify_queued`, and either invokes SageMaker or refunds before any GPU
runs. These tests use moto S3, PostgreSQL and a small recording SageMaker fake; `probe_duration`
is replaced at `dispatcher.probe_duration` because no network or real ffprobe belongs here.

Money and state come from `pg`; each refusal asserts the refund reason, the balance and whether
the upload survived, and that no endpoint call was made.
"""

import json
import uuid

import pytest
from conftest import STARTER_CENTS
from neurolens import billing, pricing, storage

ENDPOINT = "neurolens-gpu"
MAX_UPLOAD_BYTES = 300_000_000
MAX_S = 120


class RecordingSageMaker:
    """The only call the dispatcher may make: InvokeEndpointAsync, recorded."""

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def invoke_endpoint_async(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"InferenceId": kwargs["InferenceId"]}


@pytest.fixture
def probe(monkeypatch):
    """Replace dispatcher.probe_duration, returning the recorded (url, timeout_s) calls."""
    from neurolens.lambdas import dispatcher

    calls = []

    def install(seconds=3.0, error=None, on_probe=None):
        def fake(url, timeout_s=30):
            calls.append({"url": url, "timeout_s": timeout_s})
            if on_probe is not None:
                on_probe(url, timeout_s)
            if error is not None:
                raise error
            return seconds

        monkeypatch.setattr(dispatcher, "probe_duration", fake)
        return calls

    return install


def dispatch(
    aws,
    db,
    key,
    *,
    s3=None,
    sagemaker=None,
    max_upload_bytes=MAX_UPLOAD_BYTES,
    max_duration_s=MAX_S,
):
    from neurolens.lambdas import dispatcher

    return dispatcher.dispatch(
        aws.bucket,
        key,
        s3=s3 or aws.s3,
        sagemaker=sagemaker or RecordingSageMaker(),
        db=db,
        endpoint=ENDPOINT,
        max_upload_bytes=max_upload_bytes,
        max_duration_s=max_duration_s,
    )


def upload(aws, key, body=b"an uploaded video"):
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=body)


def exists(aws, key):
    return storage.object_exists(aws.s3, aws.bucket, key)


def assert_refunded(pg, job_id, reason, balance=(STARTER_CENTS, 0)):
    job = pg.job(job_id)
    assert job["status"] == "failed"
    assert job["error_code"] == reason
    assert pg.refund_reason(job_id) == reason
    assert pg.balance() == balance


# ---------------------------------------------------------------- refusals (never invoked)


def test_unknown_job_is_skipped_without_touching_the_upload(aws, db, pg, new_key, probe):
    _, key = new_key()
    upload(aws, key)
    calls = probe()
    sagemaker = RecordingSageMaker()
    assert dispatch(aws, db, key, sagemaker=sagemaker) == "skipped"
    assert sagemaker.calls == [] and calls == []
    assert exists(aws, key)


def test_a_failed_job_deletes_the_late_upload_and_skips(aws, db, pg, new_job, probe):
    job_id, key = new_job()
    upload(aws, key)
    billing.issue_refund(db, job_id, "cancelled", queued=True)
    calls = probe()
    sagemaker = RecordingSageMaker()
    assert dispatch(aws, db, key, sagemaker=sagemaker) == "skipped"
    assert sagemaker.calls == [] and calls == []
    assert not exists(aws, key)
    assert pg.balance() == (STARTER_CENTS, 0)


def test_a_done_job_keeps_its_upload_and_is_not_invoked(aws, db, pg, new_job, probe):
    job_id, key = new_job()
    upload(aws, key)
    billing.settle_success(db, job_id)
    calls = probe()
    sagemaker = RecordingSageMaker()
    assert dispatch(aws, db, key, sagemaker=sagemaker) == "skipped"
    assert sagemaker.calls == [] and calls == []
    assert exists(aws, key)  # the replay copy of a finished job


def test_a_job_claimed_while_the_file_is_probed_is_skipped(aws, db, pg, new_job, probe):
    job_id, key = new_job()
    upload(aws, key)

    def claim_meanwhile(url, timeout_s):
        assert billing.claim(db, job_id) == 1

    calls = probe(seconds=3.0, on_probe=claim_meanwhile)
    sagemaker = RecordingSageMaker()

    assert dispatch(aws, db, key, sagemaker=sagemaker) == "skipped"

    assert len(calls) == 1
    assert sagemaker.calls == []
    job = pg.job(job_id)
    assert job["status"] == "processing" and job["attempt"] == 1
    price = pricing.estimate_cost_cents(3)
    assert pg.balance() == (STARTER_CENTS - price, price)
    assert exists(aws, key)


def test_a_processing_job_is_skipped_and_changes_nothing(aws, db, pg, new_job, probe):
    job_id, key = new_job()
    upload(aws, key)
    assert billing.claim(db, job_id) == 1
    before = pg.snapshot()
    calls = probe()
    sagemaker = RecordingSageMaker()
    assert dispatch(aws, db, key, sagemaker=sagemaker) == "skipped"
    assert sagemaker.calls == [] and calls == []
    assert pg.snapshot() == before
    assert exists(aws, key)


def test_a_missing_upload_refunds_without_a_delete(aws, db, pg, new_job, probe, spy_s3):
    job_id, key = new_job()
    calls = probe()
    sagemaker = RecordingSageMaker()
    s3 = spy_s3(forbid=("delete_object", "delete_objects"))
    assert dispatch(aws, db, key, s3=s3, sagemaker=sagemaker) == "refunded"
    assert_refunded(pg, job_id, "upload_missing")
    assert sagemaker.calls == [] and calls == []
    assert not [name for name in s3.names() if name.startswith("delete")]


def test_an_oversize_upload_refunds_and_deletes(aws, db, pg, new_job, probe):
    job_id, key = new_job()
    upload(aws, key, b"0123456789A")
    calls = probe()
    sagemaker = RecordingSageMaker()
    assert dispatch(aws, db, key, sagemaker=sagemaker, max_upload_bytes=10) == "refunded"
    assert_refunded(pg, job_id, "file_too_large")
    assert sagemaker.calls == [] and calls == []
    assert not exists(aws, key)


def test_an_unreadable_video_refunds_and_deletes(aws, db, pg, new_job, probe):
    from neurolens.media import UnreadableVideo

    job_id, key = new_job()
    upload(aws, key)
    calls = probe(error=UnreadableVideo("not a readable video"))
    sagemaker = RecordingSageMaker()
    assert dispatch(aws, db, key, sagemaker=sagemaker) == "refunded"
    assert_refunded(pg, job_id, "unreadable_video")
    assert sagemaker.calls == [] and len(calls) == 1
    assert not exists(aws, key)


def test_a_too_long_video_refunds_and_deletes(aws, db, pg, new_job, probe):
    job_id, key = new_job()
    upload(aws, key)
    probe(seconds=121.0)
    sagemaker = RecordingSageMaker()
    assert dispatch(aws, db, key, sagemaker=sagemaker) == "refunded"
    assert_refunded(pg, job_id, "duration_exceeds_max_verified")
    assert sagemaker.calls == []
    assert not exists(aws, key)


def test_short_credit_for_the_real_length_refunds_and_deletes(aws, db, pg, new_job, probe):
    price = pricing.estimate_cost_cents(1)
    job_id, key = new_job(client_seconds=1, starter_cents=price)
    upload(aws, key)
    probe(seconds=61.0)  # the user has none left for the actual price
    sagemaker = RecordingSageMaker()
    assert dispatch(aws, db, key, sagemaker=sagemaker) == "refunded"
    assert_refunded(pg, job_id, "insufficient_credit_for_actual_duration", balance=(price, 0))
    assert sagemaker.calls == []
    assert not exists(aws, key)


# ---------------------------------------------------------------- the invoke


def test_a_good_job_is_invoked_with_the_specs_arguments(aws, db, pg, new_job, probe):
    job_id, key = new_job()
    upload(aws, key)
    calls = probe(seconds=3.0)
    sagemaker = RecordingSageMaker()

    assert dispatch(aws, db, key, sagemaker=sagemaker) == "invoked"

    [call] = sagemaker.calls
    assert call["EndpointName"] == ENDPOINT
    assert call["InputLocation"] == f"s3://{aws.bucket}/{key}"
    assert call["InferenceId"] == job_id
    assert call["CustomAttributes"] == job_id
    assert call["ContentType"] == "application/octet-stream"
    assert call["InvocationTimeoutSeconds"] == 3600
    assert abs(call["RequestTTLSeconds"] - 7200) <= 5
    [probed] = calls
    assert probed["url"].startswith("https://")
    assert probed["timeout_s"] == 90
    assert pg.job(job_id)["status"] == "queued"
    price = pricing.estimate_cost_cents(3)
    assert pg.balance() == (STARTER_CENTS - price, price)
    assert exists(aws, key)


def test_a_second_dispatch_reserves_no_more_credit(aws, db, pg, new_job, probe):
    job_id, key = new_job()
    upload(aws, key)
    probe(seconds=3.0)
    sagemaker = RecordingSageMaker()

    assert dispatch(aws, db, key, sagemaker=sagemaker) == "invoked"
    assert dispatch(aws, db, key, sagemaker=sagemaker) == "invoked"

    price = pricing.estimate_cost_cents(3)
    assert pg.balance() == (STARTER_CENTS - price, price)
    assert pg.kinds() == ["starter", "reserve"]
    assert pg.job(job_id)["status"] == "queued"
    assert {call["InferenceId"] for call in sagemaker.calls} == {job_id}


def test_a_job_created_two_hours_fifty_minutes_ago_gets_about_ten_minutes(
    aws, db, pg, new_job, probe
):
    job_id, key = new_job()
    upload(aws, key)
    probe(seconds=3.0)
    pg.age(job_id, created_s=2 * 3600 + 50 * 60)
    sagemaker = RecordingSageMaker()

    assert dispatch(aws, db, key, sagemaker=sagemaker) == "invoked"

    [call] = sagemaker.calls
    assert abs(call["RequestTTLSeconds"] - 600) <= 5


def test_a_job_created_two_hours_fifty_six_minutes_ago_is_refunded_deadline_passed(
    aws, db, pg, new_job, probe
):
    job_id, key = new_job()
    upload(aws, key)
    probe(seconds=3.0)
    pg.age(job_id, created_s=2 * 3600 + 56 * 60)
    sagemaker = RecordingSageMaker()

    assert dispatch(aws, db, key, sagemaker=sagemaker) == "refunded"

    assert_refunded(pg, job_id, "deadline_passed")
    assert sagemaker.calls == []
    assert not exists(aws, key)


def test_an_exception_before_the_invoke_leaves_the_job_queued_and_reserved(
    aws, db, pg, new_job, probe
):
    job_id, key = new_job()
    upload(aws, key)
    probe(error=RuntimeError("ffprobe unavailable"))
    sagemaker = RecordingSageMaker()

    with pytest.raises(RuntimeError, match="ffprobe unavailable"):
        dispatch(aws, db, key, sagemaker=sagemaker)

    job = pg.job(job_id)
    assert job["status"] == "queued" and job["verified_duration_ms"] is None
    price = pricing.estimate_cost_cents(3)
    assert pg.balance() == (STARTER_CENTS - price, price)
    assert sagemaker.calls == []
    assert exists(aws, key)


# ---------------------------------------------------------------- the SQS handler


def sqs_event(key, bucket):
    body = json.dumps(
        {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key, "size": 1}}}]}
    )
    return {
        "Records": [
            {
                "messageId": str(uuid.uuid4()),
                "receiptHandle": "receipt",
                "body": body,
                "eventSource": "aws:sqs",
                "eventSourceARN": "arn:aws:sqs:us-east-1:000000000000:neurolens-jobs",
            }
        ]
    }


def run_handler(aws, db, monkeypatch, key, sagemaker, *, max_upload_bytes=MAX_UPLOAD_BYTES):
    import neurolens.lambdas as lambdas

    monkeypatch.setattr(lambdas, "database", lambda: db)
    monkeypatch.setattr(lambdas, "s3_client", lambda: aws.s3)
    monkeypatch.setattr(lambdas, "sagemaker_runtime_client", lambda: sagemaker)
    monkeypatch.setenv("NEUROLENS_S3_BUCKET", aws.bucket)
    monkeypatch.setenv("NEUROLENS_GPU_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("NEUROLENS_MAX_UPLOAD_BYTES", str(max_upload_bytes))
    monkeypatch.setenv("NEUROLENS_MAX_DURATION_SECONDS", str(MAX_S))

    from neurolens.lambdas import dispatcher

    return dispatcher.handler(sqs_event(key, aws.bucket), None)


def test_the_handler_builds_the_clients_and_invokes_the_job(
    aws, db, pg, new_job, probe, monkeypatch
):
    job_id, key = new_job()
    upload(aws, key)
    probe(seconds=3.0)
    sagemaker = RecordingSageMaker()

    run_handler(aws, db, monkeypatch, key, sagemaker)

    [call] = sagemaker.calls
    assert call["EndpointName"] == ENDPOINT and call["InferenceId"] == job_id
    assert pg.job(job_id)["status"] == "queued"


def test_a_handler_exception_propagates_so_sqs_redelivers(aws, db, pg, new_job, probe, monkeypatch):
    job_id, key = new_job()
    upload(aws, key)
    probe(error=RuntimeError("S3 outage"))
    sagemaker = RecordingSageMaker()

    with pytest.raises(RuntimeError, match="S3 outage"):
        run_handler(aws, db, monkeypatch, key, sagemaker)

    assert pg.job(job_id)["status"] == "queued"
    price = pricing.estimate_cost_cents(3)
    assert pg.balance() == (STARTER_CENTS - price, price)
    assert sagemaker.calls == []
    assert exists(aws, key)
