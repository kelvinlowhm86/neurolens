"""
Pytest plugin shared by the common, worker and web test suites:  pytest_plugins = ["neurolens_common.testing"]

Tests run against a real Postgres (the billing logic lives in PL/pgSQL, so a fake won't do).

    TEST_DATABASE_URL=postgresql://test:test@localhost:55432/postgres pytest packages/common

A fresh `neurolens_test` database is created per session and migrated; tables are truncated
between tests.
"""

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from neurolens_common import migrate

ADMIN_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://test:test@localhost:55432/postgres")
MIGRATIONS = Path(os.environ.get("MIGRATIONS_DIR", Path(__file__).resolve().parents[3] / "migrations"))
TEST_DB = "neurolens_test"


def _db_url(name: str) -> str:
    base, _, _ = ADMIN_URL.rpartition("/")
    return f"{base}/{name}"


@pytest.fixture(scope="session")
def db_url():
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {TEST_DB}")
    url = _db_url(TEST_DB)
    migrate.apply(url, MIGRATIONS)
    os.environ["DATABASE_URL"] = url
    from neurolens_common.config import get_settings
    get_settings.cache_clear()
    yield url


@pytest.fixture
def conn(db_url):
    """Autocommit connection: each statement commits, like separate requests would."""
    with psycopg.connect(db_url, autocommit=True, row_factory=dict_row) as c:
        c.execute("TRUNCATE credit_ledger, payments, jobs, users, workers CASCADE")
        yield c


@pytest.fixture
def make_user(conn):
    def _make(credits: int = 0) -> uuid.UUID:
        uid = conn.execute(
            "INSERT INTO users (email) VALUES (%s) RETURNING id", (f"{uuid.uuid4()}@test.local",)
        ).fetchone()["id"]
        if credits:
            conn.execute("SELECT grant_credits(%s, %s, 'GRANT', %s)", (uid, credits, f"seed:{uid}"))
        return uid
    return _make


@pytest.fixture
def make_job(conn):
    from neurolens_common import jobs, s3keys

    def _make(user_id: uuid.UUID, est: float = 30.0) -> uuid.UUID:
        job_id = uuid.uuid4()
        jobs.create_job(conn, job_id=job_id, user_id=user_id, filename="ad.mp4",
                        content_type="video/mp4", size_bytes=1000,
                        s3_input_key=s3keys.input_key(user_id, job_id, ".mp4"), est_duration_s=est)
        return job_id
    return _make


def assert_ledger_consistent(conn, user_id):
    """Balances must equal the sum of ledger deltas, and reserved must equal the jobs' holds."""
    u = conn.execute("SELECT credits_available, credits_reserved FROM users WHERE id = %s", (user_id,)).fetchone()
    s = conn.execute(
        """SELECT COALESCE(sum(delta_available), 0) AS a, COALESCE(sum(delta_reserved), 0) AS r
             FROM credit_ledger WHERE user_id = %s""", (user_id,)).fetchone()
    held = conn.execute(
        "SELECT COALESCE(sum(credits_reserved), 0) AS h FROM jobs WHERE user_id = %s", (user_id,)).fetchone()
    assert u["credits_available"] == s["a"]
    assert u["credits_reserved"] == s["r"] == held["h"]
