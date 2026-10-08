"""While Aurora resumes from auto-pause, BeginTransaction can answer ThrottlingException
("insufficient resources on the database") as well as DatabaseResumingException (seen live on
the hourly reaper, 2026-10-08). Both mean "still waking" and are retried within resume_wait_s."""

import pytest
from botocore.exceptions import ClientError
from neurolens import db as dbmod

from conftest import FakeClock

CLUSTER = "arn:aws:rds:us-east-1:000000000000:cluster:neurolens-db"
SECRET = "arn:aws:secretsmanager:us-east-1:000000000000:secret:neurolens-db"


def _error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "BeginTransaction")


@pytest.fixture
def clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(dbmod, "time", clock)
    return clock


def _database(answers):
    """A Data API database whose begin_transaction gives each answer in turn (an exception is
    raised), then opens transaction "tx-1"."""
    client = dbmod.data_api_client("us-east-1")
    answers = list(answers)

    def begin_transaction(**kwargs):
        if answers:
            raise answers.pop(0)
        return {"transactionId": "tx-1"}

    client.begin_transaction = begin_transaction
    client.commit_transaction = lambda **kwargs: {}
    return dbmod.DataApiDatabase(client, CLUSTER, SECRET, "neurolens", resume_wait_s=60)


def test_a_throttle_while_waking_is_waited_out(clock):
    db = _database([_error("DatabaseResumingException"), _error("ThrottlingException")] * 2)
    with db.transaction() as tx:
        assert tx is not None
    assert clock.elapsed < 60


def test_a_throttle_past_the_wait_raises_database_waking(clock):
    db = _database([_error("ThrottlingException")] * 100)
    with pytest.raises(dbmod.DatabaseWaking):
        with db.transaction():
            pass
    assert clock.elapsed <= 60


def test_other_errors_are_not_retried(clock):
    db = _database([_error("BadRequestException")])
    with pytest.raises(ClientError):
        with db.transaction():
            pass
    assert clock.elapsed == 0
