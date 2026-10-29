"""M3h §3, §13: claiming, finishing and releasing a comparison.

A comparison is free but has at most `report.max_tries` tries, only failures use one up, and the
row is keyed by the canonical pair so B-vs-A can never start a second report. PostgreSQL is real;
nothing here calls Bedrock or S3.
"""

import uuid

import pytest
from conftest import STARTER_CENTS, USER_ID
from neurolens import billing, storage
from test_m3g_report import job_in_state

REPORT = {"max_tries": 3, "stale_minutes": 5}
MAX_TRIES = REPORT["max_tries"]
STALE_MINUTES = REPORT["stale_minutes"]

LOW = "00000000-0000-4000-8000-000000000001"
HIGH = "ffffffff-ffff-4fff-8fff-ffffffffffff"


def canonical(x, y):
    return tuple(sorted(str(uuid.UUID(str(value))) for value in (x, y)))


def job_with_id(db, job_id, state="done", *, user_id=USER_ID):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", STARTER_CENTS)
    key = f"uploads/{user_id}/{job_id}.mp4"
    billing.reserve(db, user_id, job_id, key, "clip.mp4", 3000)
    if state in ("processing", "done", "failed"):
        assert billing.claim(db, job_id) == 1
    if state == "done":
        assert billing.settle_success(db, job_id) is True
    elif state == "failed":
        assert billing.issue_refund(db, job_id, "stalled", attempt=1) is True
    return job_id


def done_pair(db):
    first, _ = job_in_state(db, "done")
    second, _ = job_in_state(db, "done")
    return canonical(first, second)


def set_comparison(pg, a, b, state="writing", *, tries=1, started_minutes_ago=0, error=None):
    pg.conn.execute(
        "INSERT INTO comparisons (job_a, job_b, state, tries, started_at, error) "
        "VALUES (%s::uuid, %s::uuid, %s, %s, now() - make_interval(mins => %s), %s) "
        "ON CONFLICT (job_a, job_b) DO UPDATE SET state = EXCLUDED.state, "
        "tries = EXCLUDED.tries, started_at = EXCLUDED.started_at, error = EXCLUDED.error",
        (a, b, state, tries, started_minutes_ago, error),
    )


def comparison(pg, a, b):
    return pg.one("SELECT * FROM comparisons WHERE job_a = %s::uuid AND job_b = %s::uuid", a, b)


def claim(db, a, b):
    return billing.claim_comparison(db, USER_ID, a, b, MAX_TRIES, STALE_MINUTES)


def test_the_first_claim_writes_a_writing_row_with_one_try(db, pg):
    a, b = done_pair(db)

    assert claim(db, a, b) is True

    row = comparison(pg, a, b)
    assert (row["state"], row["tries"], row["error"]) == ("writing", 1, None)
    assert row["started_at"] is not None


def test_b_vs_a_uses_the_same_row_and_the_same_try(db, pg):
    a, b = done_pair(db)
    assert claim(db, a, b) is True
    first_start = comparison(pg, a, b)["started_at"]

    assert claim(db, *storage.comparison_pair(b, a)) is False

    row = comparison(pg, a, b)
    assert (row["state"], row["tries"]) == ("writing", 1)
    assert row["started_at"] == first_start


def test_a_failed_comparison_can_retry_up_to_the_cap(db, pg):
    a, b = done_pair(db)

    for expected_try in (1, 2, 3):
        assert claim(db, a, b) is True
        assert billing.finish_comparison(db, a, b, "failed", "bad_answer") is True
        row = comparison(pg, a, b)
        assert (row["state"], row["tries"]) == ("failed", expected_try)

    assert claim(db, a, b) is False
    assert comparison(pg, a, b)["tries"] == MAX_TRIES


def test_a_stale_writing_comparison_is_reclaimed(db, pg):
    a, b = done_pair(db)
    set_comparison(pg, a, b, "writing", tries=1, started_minutes_ago=STALE_MINUTES + 1)

    assert claim(db, a, b) is True

    row = comparison(pg, a, b)
    assert (row["state"], row["tries"], row["error"]) == ("writing", 2, None)


def test_a_fresh_writing_comparison_is_not_reclaimed(db, pg):
    a, b = done_pair(db)
    set_comparison(pg, a, b, "writing", tries=1, started_minutes_ago=STALE_MINUTES - 1)
    before = comparison(pg, a, b)

    assert claim(db, a, b) is False

    assert comparison(pg, a, b) == before


@pytest.mark.parametrize("foreign", ["a", "b"])
def test_either_job_belonging_to_someone_else_blocks_the_claim(db, pg, foreign):
    if foreign == "a":
        job_with_id(db, LOW, user_id="someone-else")
        job_with_id(db, HIGH)
    else:
        job_with_id(db, LOW)
        job_with_id(db, HIGH, user_id="someone-else")

    assert claim(db, LOW, HIGH) is False

    assert pg.rows("SELECT * FROM comparisons") == []


@pytest.mark.parametrize("unfinished", ["a", "b"])
@pytest.mark.parametrize("state", ["queued", "processing", "failed"])
def test_either_job_not_being_done_blocks_the_claim(db, pg, unfinished, state):
    if unfinished == "a":
        job_with_id(db, LOW, state)
        job_with_id(db, HIGH)
    else:
        job_with_id(db, LOW)
        job_with_id(db, HIGH, state)

    assert claim(db, LOW, HIGH) is False

    assert pg.rows("SELECT * FROM comparisons") == []


@pytest.mark.parametrize("deleted", ["a", "b"])
def test_either_job_being_deleted_blocks_the_claim(db, pg, deleted):
    job_with_id(db, LOW)
    job_with_id(db, HIGH)
    target = LOW if deleted == "a" else HIGH
    pg.conn.execute("UPDATE jobs SET deleted_at = now() WHERE job_id = %s::uuid", (target,))

    assert claim(db, LOW, HIGH) is False

    assert pg.rows("SELECT * FROM comparisons") == []


def test_a_deleted_user_cannot_claim(db, pg):
    a, b = done_pair(db)
    pg.conn.execute("UPDATE users SET deleted_at = now() WHERE user_id = %s", (USER_ID,))

    assert claim(db, a, b) is False

    assert pg.rows("SELECT * FROM comparisons") == []


def test_releasing_a_start_failure_gives_the_try_back(db, pg):
    a, b = done_pair(db)
    assert claim(db, a, b) is True

    billing.release_comparison_try(db, a, b)

    row = comparison(pg, a, b)
    assert (row["state"], row["tries"]) == ("failed", 0)
    assert claim(db, a, b) is True
    assert comparison(pg, a, b)["tries"] == 1


def test_finish_marks_a_writing_comparison_ready(db, pg):
    a, b = done_pair(db)
    assert claim(db, a, b) is True

    assert billing.finish_comparison(db, a, b, "ready") is True

    row = comparison(pg, a, b)
    assert (row["state"], row["error"]) == ("ready", None)


@pytest.mark.parametrize("deleted", ["a", "b"])
def test_finish_does_nothing_once_either_job_is_deleted(db, pg, deleted):
    a, b = done_pair(db)
    assert claim(db, a, b) is True
    target = a if deleted == "a" else b
    pg.conn.execute("UPDATE jobs SET deleted_at = now() WHERE job_id = %s::uuid", (target,))

    assert billing.finish_comparison(db, a, b, "ready") is False

    assert comparison(pg, a, b)["state"] == "writing"


def test_the_stored_pair_is_the_one_comparison_pair_makes():
    x, y = str(uuid.uuid4()), str(uuid.uuid4())

    assert storage.comparison_pair(x, y) == canonical(x, y)
