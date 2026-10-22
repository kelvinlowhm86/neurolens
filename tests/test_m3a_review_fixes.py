"""Fixes from the M3a money-slice review: the Data API client never retries on its own, an upload
name that is not a job id is an unknown job, and a crafted huge duration is refunded as too long
by the dispatcher's `verify_queued`."""

import json

import boto3
import pytest
from botocore.config import Config
from neurolens import billing, settings, storage
from neurolens import db as dbmod

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


def test_dead_letter_handler_ignores_a_plain_name(aws, db, monkeypatch, patch_everywhere):
    from neurolens.lambdas import dlq_handler

    patch_everywhere("DataApiDatabase", lambda *a, **kw: db, "neurolens.db", "neurolens.lambdas")
    for name in ("NEUROLENS_DB_CLUSTER_ARN", "NEUROLENS_DB_SECRET_ARN", "NEUROLENS_DB_NAME"):
        monkeypatch.setenv(name, "x")
    body = {
        "Records": [{"s3": {"bucket": {"name": aws.bucket}, "object": {"key": "uploads/a.mp4"}}}]
    }
    dlq_handler.handler({"Records": [{"body": json.dumps(body)}]}, None)  # returns normally


# ---------------------------------------------------------------- 4. huge durations


def test_a_duration_beyond_the_integer_column_is_refunded_as_too_long(db, pg, new_job):
    job_id, _ = new_job()
    result = billing.verify_queued(
        db, job_id, 3_000_000_000, settings.max_duration(None)
    )  # ~833 hours > 2**31 ms
    assert result == "duration_exceeds_max_verified"
    assert pg.job(job_id)["status"] == "failed"
