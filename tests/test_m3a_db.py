"""M3a database layer: neurolens.db's PostgreSQL backend and the migrations. Written first from
docs/M3a_spec.md §3c, §3d and §10 (real PostgreSQL 16 through NEUROLENS_TEST_DSN).

The Data API backend has no scripted-reply tests (§10): infra/check_aurora.py checks it on Aurora.
"""

import re
import uuid
from datetime import datetime, timedelta

import pytest
from conftest import MIGRATIONS, migration_statements
from neurolens import billing


# ---------------------------------------------------------------- migrations


def test_migrations_hold_only_plain_statements_the_data_api_can_run_one_by_one():
    """§3c: no functions or DO blocks, each statement ending with `;` at the end of a line (the
    conftest applies them split exactly that way, so the schema itself is tested)."""
    assert sorted(p.name for p in MIGRATIONS.glob("*.sql"))[0] == "001_init.sql"
    for statement in migration_statements():
        assert "$$" not in statement
        assert not re.match(r"^\s*DO\b", statement, re.IGNORECASE)
        assert not re.search(r"\bCREATE\s+(OR\s+REPLACE\s+)?FUNCTION\b", statement, re.I)


def test_schema_has_the_five_tables(pg):
    names = {
        r["table_name"]
        for r in pg.rows(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
        )
    }
    assert {"users", "balances", "jobs", "ledger", "refunds"} <= names


# ---------------------------------------------------------------- transaction()


def test_transaction_commits_on_normal_exit(db, pg):
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO users (user_id, email) VALUES (:user_id, :email)",
            {"user_id": "u-commit", "email": "c@example.com"},
        )
    assert pg.one("SELECT email FROM users WHERE user_id = %s", "u-commit") == {
        "email": "c@example.com"
    }


def test_transaction_rolls_back_on_an_exception(db, pg):
    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO users (user_id, email) VALUES (:user_id, :email)",
                {"user_id": "u-rollback", "email": "r@example.com"},
            )
            raise Boom
    assert pg.one("SELECT 1 AS x FROM users WHERE user_id = %s", "u-rollback") is None


def test_execute_returns_a_list_of_dicts_by_column_name(db):
    with db.transaction() as tx:
        rows = tx.execute(
            "SELECT CAST(:n AS integer) AS n, CAST(:s AS text) AS s", {"n": 5, "s": "x"}
        )
    assert rows == [{"n": 5, "s": "x"}]


def test_execute_without_params_and_a_statement_with_no_rows(db):
    with db.transaction() as tx:
        assert tx.execute("SELECT user_id FROM users") == []
        tx.execute(
            "INSERT INTO users (user_id, email) VALUES (:user_id, :email)",
            {"user_id": "u-norows", "email": "n@example.com"},
        )


def test_column_types_follow_section_3d(db, new_job):
    """int, str, bool, None, aware UTC datetime for timestamptz, str for uuid, parsed jsonb."""
    job_id, _ = new_job()
    with db.transaction() as tx:
        [row] = tx.execute(
            "SELECT job_id, user_id, status, stages, attempt, reserved_cents, created_at, "
            "verified_duration_ms, CAST(:flag AS boolean) AS flag "
            "FROM jobs WHERE job_id = CAST(:job_id AS uuid)",
            {"job_id": job_id, "flag": True},
        )
    assert row["job_id"] == job_id and isinstance(row["job_id"], str)
    assert isinstance(row["user_id"], str) and isinstance(row["status"], str)
    assert row["stages"] == []
    assert type(row["attempt"]) is int and type(row["reserved_cents"]) is int
    assert row["verified_duration_ms"] is None
    assert row["flag"] is True
    created = row["created_at"]
    assert isinstance(created, datetime)
    assert created.utcoffset() == timedelta(0)


def test_jsonb_written_through_a_cast_reads_back_as_python_objects(db, new_job):
    job_id, _ = new_job()
    stages = [{"stage": "downloading", "at": "2026-10-14T03:22:10Z"}]
    import json

    with db.transaction() as tx:
        tx.execute(
            "UPDATE jobs SET stages = CAST(:stages AS jsonb) WHERE job_id = CAST(:job_id AS uuid)",
            {"stages": json.dumps(stages), "job_id": job_id},
        )
        [row] = tx.execute(
            "SELECT stages FROM jobs WHERE job_id = CAST(:job_id AS uuid)", {"job_id": job_id}
        )
    assert row["stages"] == stages


# ---------------------------------------------------------------- the :name rewrite


@pytest.mark.parametrize(
    "sql, params",
    [
        ("SELECT 5 % 2 AS x", None),
        ("SELECT CAST(:n AS integer) % 2 AS x", {"n": 5}),
        ("SELECT 'processing' AS x", None),
        ("SELECT user_id FROM users WHERE email = 'a@example.com'", None),
        ("SELECT CAST(:n AS integer) AS n, 'x' AS s", {"n": 1}),
    ],
    ids=["percent", "percent_with_params", "literal", "literal_in_where", "literal_with_params"],
)
def test_sql_with_a_percent_sign_or_a_string_literal_raises(db, sql, params):
    """§3d: application SQL never contains `%` or string literals, and the rewrite raises if it
    finds either. Every statement here is valid PostgreSQL, so the error is the rewrite's."""
    with pytest.raises(Exception):  # noqa: B017 - the spec does not name the exception type
        with db.transaction() as tx:
            tx.execute(sql, params)


def test_the_rewrite_leaves_casts_and_named_parameters_working(db):
    job_id = str(uuid.uuid4())
    with db.transaction() as tx:
        rows = tx.execute(
            "SELECT CAST(:job_id AS uuid) AS j, make_interval(secs => :s) AS i",
            {"job_id": job_id, "s": 90},
        )
    assert rows[0]["j"] == job_id


# ---------------------------------------------------------------- from_config


def test_from_config_builds_the_postgres_backend(db_dsn):
    from neurolens import db as dbmod

    database = dbmod.from_config({"db": {"backend": "postgres", "dsn": db_dsn}})
    assert isinstance(database, dbmod.PostgresDatabase)
    billing.ensure_user(database, "u-from-config", "f@example.com", 500)
    assert billing.get_balance(database, "u-from-config") == {
        "available_cents": 500,
        "reserved_cents": 0,
    }
