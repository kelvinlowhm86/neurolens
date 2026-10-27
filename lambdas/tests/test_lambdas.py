"""Lambda handlers with a fake RDS Data API that executes the SQL on the test Postgres."""

import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from neurolens_common import jobs, ledger, s3keys
from neurolens_common.reaper_sql import as_psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FakeRdsData:
    """execute_statement → psycopg, returning Data-API-shaped records."""

    def __init__(self, conn):
        self.conn = conn

    def execute_statement(self, resourceArn, secretArn, database, sql, parameters=()):
        params = {p["name"]: next(iter(p["value"].values())) for p in parameters}
        row = self.conn.execute(as_psycopg(sql), params).fetchone()
        val = next(iter(row.values()))
        return {"records": [[{"booleanValue": val} if isinstance(val, bool) else {"longValue": val}]]}


class FakeSqs:
    def __init__(self, visible=0, inflight=0):
        self.v, self.i = visible, inflight

    def get_queue_attributes(self, QueueUrl, AttributeNames):
        return {"Attributes": {"ApproximateNumberOfMessages": str(self.v),
                               "ApproximateNumberOfMessagesNotVisible": str(self.i)}}


class FakeSsm:
    def __init__(self, value):
        self.value = value

    def get_parameter(self, Name):
        return {"Parameter": {"Value": self.value}}


@pytest.fixture
def lambdas(monkeypatch, conn):
    for k, v in {"DB_CLUSTER_ARN": "arn:cluster", "DB_SECRET_ARN": "arn:secret", "JOBS_QUEUE_URL": "q",
                 "LAST_JOB_PARAM": "/neurolens/last_job_created_at", "AWS_DEFAULT_REGION": "ap-southeast-1"}.items():
        monkeypatch.setenv(k, v)
    reaper = importlib.import_module("sla_reaper")
    refund = importlib.import_module("dlq_refund")
    monkeypatch.setattr(reaper, "rds", FakeRdsData(conn))
    monkeypatch.setattr(refund, "rds", FakeRdsData(conn))
    return reaper, refund


def test_reaper_skips_db_when_idle(lambdas, monkeypatch):
    reaper, _ = lambdas
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    monkeypatch.setattr(reaper, "sqs", FakeSqs())
    monkeypatch.setattr(reaper, "ssm", FakeSsm(old))
    assert reaper.handler({}, None) == {"skipped": True}


def test_reaper_runs_rules_when_recent_activity(lambdas, monkeypatch, conn, make_user, make_job):
    reaper, _ = lambdas
    monkeypatch.setattr(reaper, "sqs", FakeSqs())
    monkeypatch.setattr(reaper, "ssm", FakeSsm(datetime.now(timezone.utc).isoformat()))
    uid = make_user(100)
    job = make_job(uid)
    ledger.reserve(conn, job, 30)
    conn.execute("UPDATE jobs SET created_at = now() - interval '45 minutes' WHERE id = %s", (job,))
    out = reaper.handler({}, None)
    assert out["closed"]["expire_uploads"] == 1
    assert ledger.balance(conn, uid) == {"credits_available": 100, "credits_reserved": 0}


def test_dlq_refund(lambdas, conn, make_user, make_job):
    _, refund = lambdas
    uid = make_user(100)
    job = make_job(uid)
    ledger.reserve(conn, job, 30)
    jobs.claim_job(conn, job, "i-1", datetime.now(timezone.utc))
    body = json.dumps({"Records": [{"eventName": "ObjectCreated:Post", "eventTime": "2026-10-27T00:00:00Z",
                                    "s3": {"bucket": {"name": "b"},
                                           "object": {"key": s3keys.input_key(uid, job, ".mp4"), "size": 1}}}]})
    event = {"Records": [{"messageId": "m1", "body": body}, {"messageId": "m2", "body": "not json"}]}
    assert refund.handler(event, None) == {"batchItemFailures": [{"itemIdentifier": "m2"}]}
    assert jobs.get_job(conn, job)["error_code"] == "RETRIES_EXHAUSTED"
    assert ledger.balance(conn, uid)["credits_reserved"] == 0
    assert refund.handler({"Records": [{"messageId": "m1", "body": body}]}, None) == {"batchItemFailures": []}
