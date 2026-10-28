"""POST /api/wake starts Aurora's resume from the landing page's first interaction (M3b §2)."""

import pytest
from botocore.exceptions import ClientError
from neurolens import db as dbmod
from neurolens.web.app import create_app

from conftest import FakeClock

CLUSTER = "arn:aws:rds:us-east-1:000000000000:cluster:neurolens-db"
SECRET = "arn:aws:secretsmanager:us-east-1:000000000000:secret:neurolens-db"


class StubDB:
    """Only wake() is used by the route; every call and an optional failure are recorded."""

    def __init__(self, error=None):
        self.calls = 0
        self.error = error

    def wake(self):
        self.calls += 1
        if self.error is not None:
            raise self.error


def test_wake_works_signed_out_and_calls_once(cognito_app):
    db = StubDB()
    client = cognito_app(database=db).test_client()

    resp = client.post("/api/wake", json={})

    assert resp.status_code == 204
    assert resp.data == b""
    assert db.calls == 1


def test_a_failed_wake_still_answers_204(cognito_app, caplog):
    db = StubDB(RuntimeError("boom"))
    client = cognito_app(database=db).test_client()

    resp = client.post("/api/wake", json={})

    assert resp.status_code == 204
    assert db.calls == 1
    assert "Wake failed" in caplog.text


def test_wake_without_a_database_answers_204(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    app.config["TESTING"] = True

    resp = app.test_client().post("/api/wake", json={})

    assert resp.status_code == 204


def test_get_wake_is_not_public(cognito_app):
    client = cognito_app(database=StubDB()).test_client()

    resp = client.get("/api/wake")

    assert resp.status_code == 401
    assert resp.get_json()["error"] == "not_signed_in"


@pytest.fixture
def clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(dbmod, "time", clock)
    return clock


def _error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "ExecuteStatement")


def _wake_database(answers):
    """A Data API database whose single execute_statement gives the next answer, then succeeds."""
    client = dbmod.data_api_client("us-east-1")
    calls = []
    answers = list(answers)

    def execute_statement(**kwargs):
        calls.append(kwargs)
        if answers:
            raise answers.pop(0)
        return {}

    client.execute_statement = execute_statement
    return dbmod.DataApiDatabase(client, CLUSTER, SECRET, "neurolens"), calls


def test_data_api_wake_starts_the_resume_with_one_call(clock):
    db, calls = _wake_database([_error("DatabaseResumingException")])

    db.wake()

    assert len(calls) == 1
    assert calls[0]["sql"] == "SELECT 1"
    assert clock.elapsed == 0


def test_data_api_wake_raises_other_errors_without_retrying(clock):
    db, calls = _wake_database([_error("BadRequestException")])

    with pytest.raises(ClientError):
        db.wake()

    assert len(calls) == 1
    assert clock.elapsed == 0


def test_postgres_wake_runs_a_statement(db):
    db.wake()
