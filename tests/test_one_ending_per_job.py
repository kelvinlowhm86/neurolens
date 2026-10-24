"""M3a 004: a job ends at most once, ever (docs/M3a_spec.md §3c, §10).

PostgreSQL tests, written first: a unique partial index on ledger (job_id) refuses a second
capture or refund row for a job, rows of other kinds and rows with no job stay unconstrained,
and the migration refuses to run against a database that already holds two refunds for a job.
"""

import importlib.util
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from conftest import MIGRATIONS, STARTER_CENTS, USER_ID
from neurolens import billing
from neurolens import db as dbmod
from psycopg.conninfo import make_conninfo

REPO_ROOT = Path(__file__).resolve().parent.parent
APPLY_SCHEMA = REPO_ROOT / "infra" / "apply_schema.py"
_spec = importlib.util.spec_from_file_location("apply_schema_under_test", APPLY_SCHEMA)
apply_schema = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apply_schema)


def reserved_job(db, *, user_id=USER_ID, client_ms=3000):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", STARTER_CENTS)
    job_id = str(uuid.uuid4())
    key = f"uploads/{user_id}/{job_id}.mp4"
    billing.reserve(db, user_id, job_id, key, "clip.mp4", client_ms)
    return job_id


def failed_job(db):
    job_id = reserved_job(db)
    assert billing.issue_refund(db, job_id, "stalled", queued=True) is True
    return job_id


def done_job(db):
    job_id = reserved_job(db)
    assert billing.settle_success(db, job_id) is True
    return job_id


LEDGER_INSERT = (
    "INSERT INTO ledger (user_id, job_id, kind, available_delta_cents, reserved_delta_cents) "
    "VALUES (%s, CAST(%s AS uuid), %s, %s, %s)"
)


def insert_ledger(pg, job_id, kind, *, user_id=USER_ID, available=0, reserved=0):
    pg.conn.execute(LEDGER_INSERT, (user_id, job_id, kind, available, reserved))


def attempt_ending_insert(dsn, job_id, kind, *, user_id=USER_ID, available=0, reserved=0):
    """Insert a ledger row on a connection that always rolls back, so a missing index leaves
    nothing behind (the test then fails on the missing exception, not on an invariant)."""
    with psycopg.connect(dsn) as conn:
        try:
            conn.execute(LEDGER_INSERT, (user_id, job_id, kind, available, reserved))
        finally:
            conn.rollback()


@pytest.mark.parametrize(
    ("setup", "kind"),
    [
        ("failed", "refund"),
        ("done", "capture"),
        ("done", "refund"),
    ],
    ids=["second_refund", "second_capture", "refund_after_capture"],
)
def test_a_second_ending_for_the_same_job_is_refused_and_changes_nothing(
    db_dsn, db, pg, setup, kind
):
    job_id = failed_job(db) if setup == "failed" else done_job(db)
    before = pg.snapshot()

    with pytest.raises(psycopg.errors.UniqueViolation):
        attempt_ending_insert(db_dsn, job_id, kind)

    assert pg.snapshot() == before


def test_other_ledger_kinds_for_the_same_job_are_not_limited(db, pg):
    job_id = done_job(db)

    with pg.conn.transaction():
        insert_ledger(pg, job_id, "reserve")
        insert_ledger(pg, job_id, "adjust")

    kinds = [row["kind"] for row in pg.ledger() if row["job_id"] == job_id]
    assert kinds.count("reserve") == 2
    assert kinds.count("adjust") == 1


def test_rows_with_no_job_are_not_limited(db, pg):
    billing.ensure_user(db, USER_ID, f"{USER_ID}@example.com", STARTER_CENTS)

    with pg.conn.transaction():
        insert_ledger(pg, None, "capture")
        insert_ledger(pg, None, "capture")

    rows = [row for row in pg.ledger() if row["job_id"] is None and row["kind"] == "capture"]
    assert len(rows) == 2


@contextmanager
def throwaway_database(pg_dsn):
    admin = psycopg.connect(pg_dsn, autocommit=True)
    name = f"nl_test_{uuid.uuid4().hex}"
    admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(pg_dsn, dbname=name)
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def test_migration_004_fails_and_changes_nothing_when_a_job_already_has_two_refunds(
    pg_dsn, tmp_path
):
    early = tmp_path / "early_migrations"
    early.mkdir()
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name[:3] in {"001", "002", "003"}:
            shutil.copy(path, early / path.name)
    assert len(list(early.glob("*.sql"))) == 3

    with throwaway_database(pg_dsn) as dsn:
        database = dbmod.from_config({"db": {"backend": "postgres", "dsn": dsn}})
        assert apply_schema.apply_migrations(database, migrations=early) == [
            "001_init.sql",
            "002_accounts.sql",
            "003_academic.sql",
        ]

        job_id = str(uuid.uuid4())
        with database.transaction() as tx:
            tx.execute(
                "INSERT INTO users (user_id, email) VALUES (:user_id, :email)",
                {"user_id": "migration-user", "email": "migration@example.com"},
            )
            tx.execute(
                "INSERT INTO balances (user_id, available_cents, reserved_cents) "
                "VALUES (:user_id, :available, :reserved)",
                {"user_id": "migration-user", "available": 0, "reserved": 0},
            )
            tx.execute(
                "INSERT INTO jobs (job_id, user_id, object_key, status, client_duration_ms, "
                "reserved_cents) VALUES (CAST(:job_id AS uuid), :user_id, :object_key, :status, "
                ":client_ms, :reserved)",
                {
                    "job_id": job_id,
                    "user_id": "migration-user",
                    "object_key": f"uploads/migration-user/{job_id}.mp4",
                    "status": "failed",
                    "client_ms": 3000,
                    "reserved": 0,
                },
            )
            for _ in range(2):
                tx.execute(
                    "INSERT INTO ledger (user_id, job_id, kind, available_delta_cents, "
                    "reserved_delta_cents) VALUES (:user_id, CAST(:job_id AS uuid), :kind, "
                    ":available, :reserved)",
                    {
                        "user_id": "migration-user",
                        "job_id": job_id,
                        "kind": "refund",
                        "available": 0,
                        "reserved": 0,
                    },
                )

        with pytest.raises(psycopg.errors.UniqueViolation):
            apply_schema.apply_migrations(database)

        with psycopg.connect(dsn) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = current_schema()"
                )
            }
            recorded = {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}

    assert "refunds" in tables
    assert not any(name.startswith("004") for name in recorded)
