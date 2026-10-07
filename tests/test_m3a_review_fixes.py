"""Fixes from the M3a money-slice review (after the §10 tests): the Data API client never retries
on its own, an upload name that is not a job id is an unknown job, a crafted huge duration is
refunded as too long, and a shutdown during the hand-back still hands the job back."""

import contextlib
import json

import boto3
import pytest
from botocore.config import Config
from neurolens import billing, storage, worker
from neurolens import db as dbmod
from neurolens.worker import Outcome

MODULES = ("neurolens.inference", "neurolens.worker")
CLUSTER = "arn:aws:rds:us-east-1:000000000000:cluster:neurolens-db"
SECRET = "arn:aws:secretsmanager:us-east-1:000000000000:secret:rds!cluster-x"


@pytest.fixture(autouse=True)
def fake_on(monkeypatch):
    monkeypatch.setenv("FAKE_INFERENCE", "1")


# ---------------------------------------------------------------- 1. no botocore retries


def test_data_api_client_makes_exactly_one_attempt():
    client = dbmod.data_api_client("us-east-1")
    assert client.meta.config.retries["total_max_attempts"] == 1
    dbmod.DataApiDatabase(client, CLUSTER, SECRET, "neurolens")  # accepted


@pytest.mark.parametrize(
    "config", [None, Config(retries={"mode": "standard"}), Config(retries={"max_attempts": 3})]
)
def test_data_api_database_refuses_a_client_that_retries(config):
    """A re-sent execute_statement runs twice inside the same transaction."""
    client = boto3.client("rds-data", region_name="us-east-1", config=config)
    with pytest.raises(ValueError, match="data_api_client"):
        dbmod.DataApiDatabase(client, CLUSTER, SECRET, "neurolens")


# ---------------------------------------------------------------- 2. non-UUID upload names


@pytest.mark.parametrize(
    "key",
    ["uploads/ad.mp4", "uploads/test-user/my ad.mov", "uploads/x/ABCDEF00-0000-4000-8000-0000.mp4"],
)
def test_a_key_that_is_not_a_job_id_has_no_job_id(key):
    assert storage.job_id_from_key(key) is None


def test_worker_skips_a_manual_upload_with_a_plain_name(aws, db, make_cfg, roi_masks_small):
    aws.s3.put_object(Bucket=aws.bucket, Key="uploads/ad.mp4", Body=b"not a job")
    outcome = worker.handle_record(
        aws.bucket,
        "uploads/ad.mp4",
        s3=aws.s3,
        db=db,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        heartbeat=lambda on_beat=None: contextlib.nullcontext(),
    )
    assert outcome is Outcome.SKIPPED


def test_dead_letter_handler_ignores_a_plain_name(aws, db, monkeypatch, patch_everywhere):
    from neurolens.lambdas import dlq_handler

    patch_everywhere("DataApiDatabase", lambda *a, **kw: db, "neurolens.db")
    for name in ("NEUROLENS_DB_CLUSTER_ARN", "NEUROLENS_DB_SECRET_ARN", "NEUROLENS_DB_NAME"):
        monkeypatch.setenv(name, "x")
    body = {
        "Records": [{"s3": {"bucket": {"name": aws.bucket}, "object": {"key": "uploads/a.mp4"}}}]
    }
    dlq_handler.handler({"Records": [{"body": json.dumps(body)}]}, None)  # returns normally


# ---------------------------------------------------------------- 4. huge durations


def test_a_duration_beyond_the_integer_column_is_refunded_as_too_long(db, pg, new_job):
    job_id, _ = new_job()
    attempt = billing.claim(db, job_id)
    result = billing.verify(db, job_id, attempt, 3_000_000_000, 120)  # ~833 hours > 2**31 ms
    assert result == "duration_exceeds_max_verified"
    assert pg.job(job_id)["status"] == "failed"


# ---------------------------------------------------------------- 3. shutdown during hand-back


def test_a_shutdown_during_the_hand_back_still_hands_the_job_back(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    real_release = billing.release_for_retry
    calls = []

    def boom(*a, **kw):
        raise RuntimeError("GPU exploded")

    def release_interrupted_once(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise worker.ShutdownRequested()  # the signal lands inside the first try
        return real_release(*args, **kwargs)

    patch_everywhere("predict", boom, *MODULES)
    patch_everywhere("release_for_retry", release_interrupted_once, "neurolens.billing")
    with pytest.raises(RuntimeError, match="GPU exploded"):  # the original error, not replaced
        worker.handle_record(
            aws.bucket,
            key,
            s3=aws.s3,
            db=db,
            cfg=make_cfg(),
            roi_masks=roi_masks_small,
            heartbeat=lambda on_beat=None: contextlib.nullcontext(),
        )
    assert len(calls) == 2
    assert pg.job(job_id)["status"] == "queued"
    assert pg.job(job_id)["error_message"] == worker.INTERRUPTED
