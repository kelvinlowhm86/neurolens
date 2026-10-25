"""M3f review fixes (docs/M3f_spec.md §5c, §5d, §7, §10).

The RDS Data API sends an integer as a bigint (`longValue`); PostgreSQL's `make_interval`
needs an integer argument, so every `make_interval(days => ...)` must cast its parameter.
These tests run the real queries through a thin wrapper that casts an uncast days parameter
to bigint, which is what the Data API binds.
"""

import contextlib
import re
import uuid

import pytest
from conftest import WEB_USER_ID
from neurolens import billing, storage
from neurolens.lambdas import reaper

STARTER_CENTS = 10_000
PRICE_CENTS = 90
DAY = 24 * 60 * 60

_DAYS_INTERVAL = re.compile(
    r"make_interval\(\s*days\s*=>\s*:([A-Za-z_][A-Za-z0-9_]*)\s*\)", re.IGNORECASE
)


class _BigIntDaysTransaction:
    def __init__(self, tx):
        self._tx = tx

    def execute(self, sql, params=None):
        sql = _DAYS_INTERVAL.sub(r"make_interval(days => CAST(:\1 AS bigint))", sql)
        return self._tx.execute(sql, params)

    def execute_unchecked(self, sql):
        return self._tx.execute_unchecked(sql)


class BigIntDaysDatabase:
    """The test database with every uncast `make_interval(days => ...)` bound as a bigint."""

    def __init__(self, db):
        self._db = db

    @contextlib.contextmanager
    def transaction(self):
        with self._db.transaction() as tx:
            yield _BigIntDaysTransaction(tx)


def curve_args(job_id):
    snapshot_id = str(uuid.uuid4())
    return {
        "snapshot_id": snapshot_id,
        "raw_key": f"dataset/{job_id}/retention/{snapshot_id}.json",
        "csv_key": f"dataset/{job_id}/retention/{snapshot_id}.csv",
        "curve_sha256": "bigint-curve",
        "points": [(i / 19, 1.0 - 0.5 * i / 19) for i in range(20)],
        "quality_flags": [],
        "views": "lt_1k",
        "published": "week",
        "promoted": False,
        "reward_cap": 5,
        "reward_window_days": 30,
    }


def make_job(db):
    billing.ensure_user(db, WEB_USER_ID, f"{WEB_USER_ID}@example.com", STARTER_CENTS)
    job_id = str(uuid.uuid4())
    key = storage.object_key(WEB_USER_ID, job_id, "video/mp4")
    billing.reserve(db, WEB_USER_ID, job_id, key, "clip.mp4", 27_400, research_consent=True)
    assert billing.claim(db, job_id) == 1
    assert billing.settle_success(db, job_id) is True
    billing.record_video_hash(db, job_id, "a" * 64)
    return job_id


def run_or_fail(call, label):
    try:
        return call()
    except Exception as err:
        pytest.fail(f"{label} does not accept a bigint days parameter: {type(err).__name__}: {err}")


def test_add_curve_accepts_a_bigint_reward_window(db):
    job_id = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)

    result = run_or_fail(
        lambda: billing.add_curve(
            BigIntDaysDatabase(db), WEB_USER_ID, job_id, **curve_args(job_id)
        ),
        "add_curve",
    )

    assert result["rewarded_cents"] == PRICE_CENTS


def test_expired_dataset_jobs_accepts_a_bigint_day_count(db, pg):
    job_id = make_job(db)
    pg.age(job_id, created_s=91 * DAY)

    jobs = run_or_fail(
        lambda: reaper.expired_dataset_jobs(BigIntDaysDatabase(db), 90),
        "expired_dataset_jobs",
    )

    assert [str(job["job_id"]) for job in jobs] == [job_id]


def test_expired_sessions_accepts_a_bigint_day_count(db, pg):
    pg.conn.execute(
        "INSERT INTO sessions (session_hash, user_id, last_seen_at) "
        "VALUES (%s, %s, now() - interval '31 days')",
        ("old-session", WEB_USER_ID),
    )

    run_or_fail(
        lambda: reaper.expired_sessions(BigIntDaysDatabase(db)),
        "expired_sessions",
    )

    assert (
        pg.one("SELECT 1 AS present FROM sessions WHERE session_hash = %s", "old-session") is None
    )
