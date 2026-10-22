"""M3e billing: `verify_queued` and the fields `job_state` gains (docs/M3e_spec.md §3, §3a).

The dispatcher prices a job before any GPU runs, so the old attempt-guarded `verify` becomes a
queued-only check under the job's row lock. Every test reads money and job state directly from
PostgreSQL (`pg`), never through the code under test.

Prices (neurolens.pricing): a started 30 s block with a half-second allowance costs 90 cents, so
27.4 s and 3 s cost 90, 61 s costs 270, and 120 s costs 360.
"""

import uuid

import pytest
from conftest import STARTER_CENTS
from neurolens import billing

MAX_S = 120


def queued(new_job, *, client_seconds=27.4, starter_cents=STARTER_CENTS):
    """A reserved job exactly as the presign endpoint leaves it (90 cents for 27.4 s)."""
    job_id, key = new_job(client_seconds=client_seconds, starter_cents=starter_cents)
    return job_id, key


# ---------------------------------------------------------------- the old verify cases


def test_equal_price_changes_no_money(db, pg, new_job):
    job_id, _ = queued(new_job)
    assert billing.verify_queued(db, job_id, 28000, MAX_S) == "ok"
    assert pg.balance() == (410, 90)
    assert pg.kinds() == ["starter", "reserve"]
    job = pg.job(job_id)
    assert job["verified_duration_ms"] == 28000 and job["reserved_cents"] == 90


def test_higher_price_and_enough_credit_reserves_the_difference(db, pg, new_job):
    job_id, _ = queued(new_job)
    assert billing.verify_queued(db, job_id, 61000, MAX_S) == "ok"  # 270
    assert pg.balance() == (230, 270)
    adjust = pg.ledger()[-1]
    assert adjust["kind"] == "adjust" and adjust["job_id"] == job_id
    assert (adjust["available_delta_cents"], adjust["reserved_delta_cents"]) == (-180, 180)
    assert pg.job(job_id)["reserved_cents"] == 270


def test_higher_price_with_exactly_enough_credit_is_covered(db, pg, new_job):
    job_id, _ = queued(new_job, starter_cents=270)  # 180 left; the difference is 180
    assert billing.verify_queued(db, job_id, 61000, MAX_S) == "ok"
    assert pg.balance() == (0, 270)


def test_higher_price_without_enough_credit_refunds_in_full(db, pg, new_job):
    job_id, _ = queued(new_job, starter_cents=200)  # 90 reserved, 110 left; 120 s costs 360
    result = billing.verify_queued(db, job_id, 120000, MAX_S)
    assert result == "insufficient_credit_for_actual_duration"
    assert pg.balance() == (200, 0)
    job = pg.job(job_id)
    assert job["status"] == "failed"
    assert job["error_code"] == "insufficient_credit_for_actual_duration"
    assert pg.refund_reason(job_id) == "insufficient_credit_for_actual_duration"
    assert "adjust" not in pg.kinds()


def test_lower_price_returns_the_difference_at_once(db, pg, new_job):
    job_id, _ = queued(new_job, client_seconds=61)  # 270 reserved
    assert billing.verify_queued(db, job_id, 20000, MAX_S) == "ok"  # 90
    assert pg.balance() == (410, 90)
    adjust = pg.ledger()[-1]
    assert adjust["kind"] == "adjust"
    assert (adjust["available_delta_cents"], adjust["reserved_delta_cents"]) == (180, -180)
    assert pg.job(job_id)["reserved_cents"] == 90


@pytest.mark.parametrize("starter", [500, 1_000_000], ids=["normal_balance", "large_balance"])
def test_over_the_maximum_refunds_before_any_adjustment(db, pg, new_job, starter):
    job_id, _ = queued(new_job, starter_cents=starter)
    result = billing.verify_queued(db, job_id, 121000, MAX_S)
    assert result == "duration_exceeds_max_verified"
    assert pg.balance() == (starter, 0)
    assert pg.kinds() == ["starter", "reserve", "refund"]
    assert pg.refund_reason(job_id) == "duration_exceeds_max_verified"


def test_exactly_at_the_maximum_is_accepted(db, pg, new_job):
    job_id, _ = queued(new_job, client_seconds=120)
    assert billing.verify_queued(db, job_id, 120000, MAX_S) == "ok"
    job = pg.job(job_id)
    assert job["status"] == "queued" and job["reserved_cents"] == 360


def test_uses_the_given_maximum(db, new_job):
    job_id, _ = queued(new_job)
    assert billing.verify_queued(db, job_id, 46000, 45) == "duration_exceeds_max_verified"


# ---------------------------------------------------------------- queued-only guard


def test_a_processing_job_is_not_queued_and_moves_no_money(db, pg, new_job):
    job_id, _ = queued(new_job)
    assert billing.claim(db, job_id) == 1
    before = pg.snapshot()
    assert billing.verify_queued(db, job_id, 61000, MAX_S) == "not_queued"
    assert pg.snapshot() == before


@pytest.mark.parametrize("terminal", ["done", "failed"])
def test_a_terminal_job_is_not_queued_and_moves_no_money(db, pg, new_job, terminal):
    job_id, _ = queued(new_job)
    if terminal == "done":
        billing.settle_success(db, job_id)
    else:
        billing.issue_refund(db, job_id, "upload_missing", queued_before_s=0)
    before = pg.snapshot()
    assert billing.verify_queued(db, job_id, 61000, MAX_S) == "not_queued"
    assert pg.snapshot() == before


def test_an_unknown_job_is_not_queued(db, pg):
    before = pg.snapshot()
    assert billing.verify_queued(db, str(uuid.uuid4()), 3000, MAX_S) == "not_queued"
    assert pg.snapshot() == before


# ---------------------------------------------------------------- idempotence and job_state


def test_a_second_verify_with_the_same_duration_moves_no_more_money(db, pg, new_job):
    job_id, _ = queued(new_job)
    assert billing.verify_queued(db, job_id, 61000, MAX_S) == "ok"
    after_first = pg.snapshot()
    assert billing.verify_queued(db, job_id, 61000, MAX_S) == "ok"
    assert pg.snapshot() == after_first
    assert pg.kinds().count("adjust") == 1


def test_job_state_reports_the_object_key_and_verified_duration(db, new_job):
    job_id, key = queued(new_job)
    state = billing.job_state(db, job_id)
    assert state["object_key"] == key
    assert state["verified_duration_ms"] is None
    assert billing.verify_queued(db, job_id, 3000, MAX_S) == "ok"
    assert billing.job_state(db, job_id)["verified_duration_ms"] == 3000
