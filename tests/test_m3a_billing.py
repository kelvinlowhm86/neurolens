"""M3a credit billing (neurolens.billing) against a real PostgreSQL 16. Written first from
docs/M3a_spec.md §3b, §4 and §10. The four ledger invariants are checked after every test by the
`db_dsn` fixture in conftest.py.

Prices: $0.90 per started 30 s block with a half-second allowance (neurolens.pricing), so 3 s and
27.4 s cost 90 cents, 61 s costs 270 and 120 s costs 360.
"""

import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import STARTER_CENTS, USER_ID
from neurolens import billing
from neurolens.db import PostgresDatabase

MAX_S = 120  # max_video_duration_seconds


def make_user(db, user_id=USER_ID, starter_cents=STARTER_CENTS):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", starter_cents)


def reserve(db, client_ms=27400, user_id=USER_ID):
    job_id = str(uuid.uuid4())
    key = f"uploads/{user_id}/{job_id}.mp4"
    price = billing.reserve(db, user_id, job_id, key, "ad.mp4", client_ms)
    return job_id, price


def claimed(db, client_ms=27400, user_id=USER_ID, starter_cents=STARTER_CENTS):
    """A user with credit and one job claimed by a worker (attempt 1)."""
    make_user(db, user_id, starter_cents)
    job_id, _ = reserve(db, client_ms, user_id)
    assert billing.claim(db, job_id) == 1
    return job_id


def money(pg, user_id=USER_ID):
    return pg.balance(user_id)


# ---------------------------------------------------------------- ensure_user / get_balance


def test_ensure_user_grants_the_starter_credit_with_one_starter_ledger_row(db, pg):
    billing.ensure_user(db, "u1", "u1@example.com", 500)
    assert billing.get_balance(db, "u1") == {"available_cents": 500, "reserved_cents": 0}
    [row] = pg.ledger("u1")
    assert row["kind"] == "starter"
    assert (row["available_delta_cents"], row["reserved_delta_cents"]) == (500, 0)
    assert row["job_id"] is None


def test_ensure_user_again_changes_nothing_not_even_the_email(db, pg):
    billing.ensure_user(db, "u1", "first@example.com", 500)
    before = pg.snapshot()
    assert billing.ensure_user(db, "u1", "second@example.com", 900) is None
    assert pg.snapshot() == before
    assert (
        pg.one("SELECT email FROM users WHERE user_id = %s", "u1")["email"] == "first@example.com"
    )


def test_starter_credit_follows_the_argument(db):
    billing.ensure_user(db, "u2", "u2@example.com", 123)
    assert billing.get_balance(db, "u2") == {"available_cents": 123, "reserved_cents": 0}


def test_two_simultaneous_first_requests_both_succeed_and_grant_the_starter_once(
    db_dsn, pg, row_lock
):
    """Deterministic: a third connection holds an uncommitted insert of the same user, so both
    calls are blocked at the same point; then it rolls back and they race for real."""
    lock = row_lock()
    lock.execute("INSERT INTO users (user_id, email) VALUES (%s, %s)", "u-race", "x@example.com")
    errors, done = [], []

    def call():
        try:
            billing.ensure_user(PostgresDatabase(db_dsn), "u-race", "u-race@example.com", 500)
            done.append(True)
        except Exception as err:  # reported below
            errors.append(err)

    threads = [threading.Thread(target=call) for _ in range(2)]
    for t in threads:
        t.start()
    lock.wait_for_blocked(2)
    lock.release()
    for t in threads:
        t.join(10)
    assert not any(t.is_alive() for t in threads)
    assert errors == [] and len(done) == 2
    assert money(pg, "u-race") == (500, 0)
    assert pg.kinds("u-race") == ["starter"]


# ---------------------------------------------------------------- Stage 1: reserve


def test_reserve_moves_the_exact_price_and_writes_the_job_and_one_reserve_row(db, pg):
    make_user(db)
    job_id = str(uuid.uuid4())
    key = f"uploads/{USER_ID}/{job_id}.mp4"
    price = billing.reserve(db, USER_ID, job_id, key, "ad_variant_1.mp4", 27400)
    assert price == 90
    assert money(pg) == (410, 90)
    job = pg.job(job_id)
    assert job["user_id"] == USER_ID
    assert job["object_key"] == key
    assert job["filename"] == "ad_variant_1.mp4"
    assert job["status"] == "queued"
    assert job["client_duration_ms"] == 27400
    assert job["reserved_cents"] == 90
    assert job["attempt"] == 0
    assert job["captured_cents"] is None and job["error_code"] is None
    row = pg.ledger()[-1]
    assert row["kind"] == "reserve"
    assert row["job_id"] == job_id
    assert (row["available_delta_cents"], row["reserved_delta_cents"]) == (-90, 90)


@pytest.mark.parametrize(
    "client_ms, price", [(1000, 90), (30500, 90), (30600, 180), (61000, 270), (120000, 360)]
)
def test_reserve_prices_with_estimate_cost_cents(db, client_ms, price):
    make_user(db, starter_cents=1000)
    assert reserve(db, client_ms)[1] == price


def test_reserve_with_too_little_credit_raises_and_changes_nothing(db, pg):
    make_user(db, starter_cents=100)
    reserve(db, 27400)  # 90 of 100: 10 left
    before = pg.snapshot()
    job_id = str(uuid.uuid4())
    with pytest.raises(billing.InsufficientCredit):
        billing.reserve(db, USER_ID, job_id, f"uploads/{USER_ID}/{job_id}.mp4", "a.mp4", 27400)
    assert pg.snapshot() == before
    assert pg.job(job_id) is None


def test_reserve_of_exactly_the_available_credit_succeeds(db, pg):
    make_user(db, starter_cents=90)
    reserve(db, 27400)
    assert money(pg) == (0, 90)


# ---------------------------------------------------------------- claim / job_state / touch


def test_claim_returns_attempt_1_and_marks_the_job_processing(db, pg):
    make_user(db)
    job_id, _ = reserve(db)
    assert billing.claim(db, job_id) == 1
    job = pg.job(job_id)
    assert job["status"] == "processing" and job["attempt"] == 1
    assert job["stages"] == [] and job["stage"] is None


def test_a_job_claimed_moments_ago_cannot_be_claimed_again(db):
    job_id = claimed(db)
    assert billing.claim(db, job_id) is None


def test_claim_of_an_unknown_job_returns_none(db):
    assert billing.claim(db, str(uuid.uuid4())) is None


def test_claim_staleness_30s_cannot_120s_can(db, pg):
    """§10: a processing job touched 30 s ago cannot be claimed; one touched 120 s ago can."""
    job_id = claimed(db)
    pg.age(job_id, updated_s=30)
    assert billing.claim(db, job_id) is None
    pg.age(job_id, updated_s=120)
    assert billing.claim(db, job_id) == 2
    assert pg.job(job_id)["attempt"] == 2


def test_each_attempt_starts_with_empty_stages(db, pg):
    job_id = claimed(db)
    assert billing.set_stage(db, job_id, 1, "downloading") is True
    assert billing.release_for_retry(db, job_id, 1, "RuntimeError: boom") is True
    assert billing.claim(db, job_id) == 2
    job = pg.job(job_id)
    assert job["stages"] == [] and job["stage"] is None


def test_a_terminal_job_can_never_be_claimed(db, pg):
    done_job = claimed(db)
    billing.settle_success(db, done_job)
    refunded, _ = reserve(db)
    billing.issue_refund(db, refunded, "upload_not_received", queued_before_s=0)
    for job_id in (done_job, refunded):
        pg.age(job_id, updated_s=3600)
        assert billing.claim(db, job_id) is None


def test_job_state_reports_status_updated_at_and_attempt(db):
    assert billing.job_state(db, str(uuid.uuid4())) is None
    job_id = claimed(db)
    state = billing.job_state(db, job_id)
    assert set(state) == {"status", "updated_at", "attempt"}
    assert state["status"] == "processing" and state["attempt"] == 1
    assert state["updated_at"].utcoffset() == timedelta(0)
    assert abs(datetime.now(UTC) - state["updated_at"]) < timedelta(minutes=1)


def test_touch_refreshes_updated_at(db, pg):
    job_id = claimed(db)
    pg.age(job_id, updated_s=80)
    assert billing.touch(db, job_id, 1) is True
    assert billing.claim(db, job_id) is None  # fresh again: not claimable as stale
    age = pg.one("SELECT now() - updated_at AS a FROM jobs WHERE job_id = %s::uuid", job_id)["a"]
    assert age < timedelta(seconds=5)


def test_set_stage_appends_stage_and_utc_time_in_the_status_format(db, pg):
    job_id = claimed(db)
    t0 = datetime(2026, 10, 14, 3, 22, 10, tzinfo=UTC)
    t1 = datetime(2026, 10, 14, 3, 22, 41, tzinfo=UTC)
    assert billing.set_stage(db, job_id, 1, "downloading", now=t0) is True
    assert billing.set_stage(db, job_id, 1, "transcribing", now=t1) is True
    job = pg.job(job_id)
    assert job["stage"] == "transcribing"
    assert job["stages"] == [
        {"stage": "downloading", "at": "2026-10-14T03:22:10Z"},
        {"stage": "transcribing", "at": "2026-10-14T03:22:41Z"},
    ]


def test_set_stage_without_now_uses_the_current_time(db, pg):
    job_id = claimed(db)
    billing.set_stage(db, job_id, 1, "downloading")
    at = datetime.strptime(pg.job(job_id)["stages"][0]["at"], "%Y-%m-%dT%H:%M:%SZ")
    assert abs(datetime.now(UTC) - at.replace(tzinfo=UTC)) < timedelta(minutes=1)


def test_release_for_retry_requeues_records_the_message_and_moves_no_money(db, pg):
    job_id = claimed(db)
    before = money(pg)
    ledger = pg.ledger()
    pg.age(job_id, updated_s=60)
    assert billing.release_for_retry(db, job_id, 1, "RuntimeError: model crashed") is True
    job = pg.job(job_id)
    assert job["status"] == "queued"
    assert job["error_message"] == "RuntimeError: model crashed"
    assert job["error_code"] is None
    assert money(pg) == before and pg.ledger() == ledger
    assert billing.job_state(db, job_id)["updated_at"] > datetime.now(UTC) - timedelta(seconds=30)
    assert billing.claim(db, job_id) == 2  # the next worker can claim it at once


# ---------------------------------------------------------------- Stage 2: verify


def test_verify_equal_price_changes_no_money(db, pg):
    job_id = claimed(db, 27400)  # 90
    assert billing.verify(db, job_id, 1, 28000, MAX_S) == "ok"
    assert money(pg) == (410, 90)
    assert pg.kinds() == ["starter", "reserve"]
    job = pg.job(job_id)
    assert job["verified_duration_ms"] == 28000 and job["reserved_cents"] == 90


def test_verify_higher_and_covered_reserves_the_difference(db, pg):
    job_id = claimed(db, 27400)  # 90 reserved
    assert billing.verify(db, job_id, 1, 61000, MAX_S) == "ok"  # 270
    assert money(pg) == (230, 270)
    adjust = pg.ledger()[-1]
    assert adjust["kind"] == "adjust" and adjust["job_id"] == job_id
    assert (adjust["available_delta_cents"], adjust["reserved_delta_cents"]) == (-180, 180)
    assert pg.job(job_id)["reserved_cents"] == 270


def test_verify_higher_with_exactly_enough_credit_is_covered(db, pg):
    job_id = claimed(db, 27400, starter_cents=270)  # 180 left, the difference is 180
    assert billing.verify(db, job_id, 1, 61000, MAX_S) == "ok"
    assert money(pg) == (0, 270)


def test_verify_higher_and_not_covered_refunds_in_full(db, pg):
    job_id = claimed(db, 27400, starter_cents=200)  # 90 reserved, 110 left; 120 s costs 360
    result = billing.verify(db, job_id, 1, 120000, MAX_S)
    assert result == "insufficient_credit_for_actual_duration"
    assert money(pg) == (200, 0)
    job = pg.job(job_id)
    assert job["status"] == "failed"
    assert job["error_code"] == "insufficient_credit_for_actual_duration"
    assert pg.refund_reason(job_id) == "insufficient_credit_for_actual_duration"
    assert "adjust" not in pg.kinds()


def test_verify_lower_returns_the_difference_at_once(db, pg):
    job_id = claimed(db, 61000)  # 270 reserved
    assert billing.verify(db, job_id, 1, 20000, MAX_S) == "ok"  # 90
    assert money(pg) == (410, 90)
    adjust = pg.ledger()[-1]
    assert adjust["kind"] == "adjust"
    assert (adjust["available_delta_cents"], adjust["reserved_delta_cents"]) == (180, -180)
    assert pg.job(job_id)["reserved_cents"] == 90


@pytest.mark.parametrize("starter", [500, 1_000_000], ids=["normal_balance", "large_balance"])
def test_verify_over_the_maximum_refunds_before_any_adjustment(db, pg, starter):
    """§4a step 1: the duration check comes before any credit logic, whatever the balance."""
    job_id = claimed(db, 27400, starter_cents=starter)
    result = billing.verify(db, job_id, 1, 121000, MAX_S)
    assert result == "duration_exceeds_max_verified"
    assert money(pg) == (starter, 0)
    assert pg.kinds() == ["starter", "reserve", "refund"]  # no adjust row first
    assert pg.job(job_id)["error_code"] == "duration_exceeds_max_verified"
    assert pg.refund_reason(job_id) == "duration_exceeds_max_verified"


def test_verify_exactly_at_the_maximum_is_accepted(db, pg):
    job_id = claimed(db, 120000)
    assert billing.verify(db, job_id, 1, 120000, MAX_S) == "ok"
    assert pg.job(job_id)["status"] == "processing"


def test_verify_uses_the_given_maximum(db):
    job_id = claimed(db, 27400)
    assert billing.verify(db, job_id, 1, 46000, 45) == "duration_exceeds_max_verified"


# ---------------------------------------------------------------- Stage 3: settle_success


def test_settle_success_captures_exactly_the_verified_price(db, pg):
    job_id = claimed(db, 27400)
    billing.verify(db, job_id, 1, 61000, MAX_S)  # 270
    billing.set_stage(db, job_id, 1, "downloading")
    assert billing.settle_success(db, job_id) is True
    assert money(pg) == (230, 0)
    capture = pg.ledger()[-1]
    assert capture["kind"] == "capture" and capture["job_id"] == job_id
    assert (capture["available_delta_cents"], capture["reserved_delta_cents"]) == (0, -270)
    job = pg.job(job_id)
    assert job["status"] == "done"
    assert job["captured_cents"] == 270
    assert job["error_code"] is None and job["error_message"] is None


def test_settle_success_clears_an_earlier_attempts_error(db, pg):
    job_id = claimed(db)
    billing.release_for_retry(db, job_id, 1, "RuntimeError: first attempt failed")
    assert billing.claim(db, job_id) == 2
    assert billing.settle_success(db, job_id) is True
    assert pg.job(job_id)["error_message"] is None


def test_settle_success_needs_no_attempt_and_works_on_a_queued_job(db, pg):
    """A finished result is always charged, whoever finished it (§4a)."""
    make_user(db)
    job_id, _ = reserve(db)
    assert billing.settle_success(db, job_id) is True
    assert pg.job(job_id)["status"] == "done"
    assert money(pg) == (410, 0)


# ---------------------------------------------------------------- exclusivity


def test_settle_then_refund_gives_one_outcome(db, pg):
    job_id = claimed(db)
    assert billing.settle_success(db, job_id) is True
    before = pg.snapshot()
    assert billing.issue_refund(db, job_id, "stalled", attempt=1) is False
    assert billing.issue_refund(db, job_id, "processing_failed", queued_before_s=0) is False
    assert billing.issue_refund(db, job_id, "stalled", processing_stale_s=0) is False
    assert pg.snapshot() == before
    assert pg.job(job_id)["status"] == "done"


def test_refund_then_settle_gives_one_outcome(db, pg):
    job_id = claimed(db)
    assert billing.issue_refund(db, job_id, "unreadable_video", "Not a video.", attempt=1) is True
    before = pg.snapshot()
    assert billing.settle_success(db, job_id) is False
    assert pg.snapshot() == before
    job = pg.job(job_id)
    assert job["status"] == "failed"
    assert job["error_code"] == "unreadable_video" and job["error_message"] == "Not a video."
    assert money(pg) == (500, 0)


def test_repeated_settle_and_refund_are_no_ops(db, pg):
    done_job = claimed(db)
    assert billing.settle_success(db, done_job) is True
    refunded, _ = reserve(db)
    assert billing.issue_refund(db, refunded, "presign_failed", queued_before_s=0) is True
    before = pg.snapshot()
    assert billing.settle_success(db, done_job) is False
    assert billing.issue_refund(db, refunded, "presign_failed", queued_before_s=0) is False
    assert pg.snapshot() == before


def test_issue_refund_without_a_condition_raises_value_error_and_changes_nothing(db, pg):
    job_id = claimed(db)
    before = pg.snapshot()
    with pytest.raises(ValueError):
        billing.issue_refund(db, job_id, "stalled")
    assert pg.snapshot() == before


def test_issue_refund_returns_the_reservation_and_records_reason_and_message(db, pg):
    job_id = claimed(db, 61000)  # 270
    assert billing.issue_refund(db, job_id, "upload_missing", "The upload is gone.", attempt=1)
    assert money(pg) == (500, 0)
    refund = pg.ledger()[-1]
    assert refund["kind"] == "refund" and refund["job_id"] == job_id
    assert (refund["available_delta_cents"], refund["reserved_delta_cents"]) == (270, -270)
    job = pg.job(job_id)
    assert job["status"] == "failed"
    assert (job["error_code"], job["error_message"]) == ("upload_missing", "The upload is gone.")
    assert pg.refund_reason(job_id) == "upload_missing"


# ---------------------------------------------------------------- attempt guards


def reclaimed(db, pg, **kw):
    """A job whose first worker went quiet and was re-claimed by a second (attempt 2)."""
    job_id = claimed(db, **kw)
    pg.age(job_id, updated_s=120)
    assert billing.claim(db, job_id) == 2
    return job_id


def test_after_a_reclaim_every_attempt_1_call_reports_a_lost_claim_and_moves_no_money(db, pg):
    job_id = reclaimed(db, pg)
    before = pg.snapshot()
    assert billing.touch(db, job_id, 1) is False
    assert billing.set_stage(db, job_id, 1, "downloading") is False
    assert billing.verify(db, job_id, 1, 61000, MAX_S) == "lost_claim"
    assert billing.verify(db, job_id, 1, 500000, MAX_S) == "lost_claim"
    assert billing.release_for_retry(db, job_id, 1, "late error") is False
    assert billing.issue_refund(db, job_id, "unreadable_video", attempt=1) is False
    assert pg.snapshot() == before


def test_after_a_reclaim_attempt_2_still_works(db, pg):
    job_id = reclaimed(db, pg)
    assert billing.touch(db, job_id, 2) is True
    assert billing.set_stage(db, job_id, 2, "downloading") is True
    assert billing.verify(db, job_id, 2, 28000, MAX_S) == "ok"


def test_after_a_reaper_refund_verify_with_the_old_attempt_is_a_lost_claim(db, pg):
    job_id = claimed(db)
    pg.age(job_id, updated_s=700)
    assert billing.issue_refund(db, job_id, "stalled", processing_stale_s=600) is True
    before = pg.snapshot()
    assert billing.verify(db, job_id, 1, 61000, MAX_S) == "lost_claim"
    assert billing.touch(db, job_id, 1) is False
    assert billing.set_stage(db, job_id, 1, "transcribing") is False
    assert billing.release_for_retry(db, job_id, 1, "x") is False
    assert pg.snapshot() == before


def test_a_late_touch_after_settlement_changes_nothing(db, pg):
    job_id = claimed(db)
    billing.settle_success(db, job_id)
    before = pg.snapshot()
    assert billing.touch(db, job_id, 1) is False
    assert pg.snapshot() == before


# ---------------------------------------------------------------- refund guards


def test_reaper_style_refund_of_a_job_a_worker_has_just_claimed_returns_false(db, pg):
    make_user(db)
    job_id, _ = reserve(db)
    pg.age(job_id, updated_s=7200, created_s=7200)  # looked abandoned to the reaper's query
    assert billing.claim(db, job_id) == 1  # a worker takes it before the reaper's refund
    before = pg.snapshot()
    assert billing.issue_refund(db, job_id, "upload_not_received", queued_before_s=3600) is False
    assert pg.snapshot() == before


def test_queued_before_refunds_only_a_queued_job_old_enough(db, pg):
    make_user(db)
    job_id, _ = reserve(db)
    pg.age(job_id, updated_s=1800)
    assert billing.issue_refund(db, job_id, "upload_not_received", queued_before_s=3600) is False
    pg.age(job_id, updated_s=3700)
    assert billing.issue_refund(db, job_id, "upload_not_received", queued_before_s=3600) is True
    assert pg.job(job_id)["status"] == "failed"


def test_queued_before_zero_refunds_any_queued_job(db, pg):
    make_user(db)
    job_id, _ = reserve(db)
    assert billing.issue_refund(db, job_id, "presign_failed", queued_before_s=0) is True
    assert money(pg) == (500, 0)


def test_dlq_style_refund_of_a_freshly_claimed_job_returns_false(db, pg):
    job_id = claimed(db)
    before = pg.snapshot()
    assert (
        billing.issue_refund(
            db, job_id, "processing_failed", "x", queued_before_s=0, processing_stale_s=90
        )
        is False
    )
    assert pg.snapshot() == before


def test_dlq_style_refund_of_a_stale_processing_job_succeeds(db, pg):
    job_id = claimed(db)
    billing.release_for_retry(db, job_id, 1, "RuntimeError: boom")
    assert billing.claim(db, job_id) == 2
    pg.age(job_id, updated_s=120)
    assert (
        billing.issue_refund(
            db,
            job_id,
            "processing_failed",
            "RuntimeError: boom",
            queued_before_s=0,
            processing_stale_s=90,
        )
        is True
    )
    job = pg.job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "processing_failed"


def test_processing_stale_does_not_refund_a_queued_job(db, pg):
    make_user(db)
    job_id, _ = reserve(db)
    pg.age(job_id, updated_s=7200)
    assert billing.issue_refund(db, job_id, "stalled", processing_stale_s=600) is False


def test_attempt_condition_does_not_refund_a_queued_job(db, pg):
    job_id = claimed(db)
    billing.release_for_retry(db, job_id, 1, "x")
    assert billing.issue_refund(db, job_id, "unreadable_video", attempt=1) is False


# ---------------------------------------------------------------- concurrency, made deterministic


def run_blocked(row_lock, job_id, *calls):
    """Hold FOR UPDATE on the job row, start one thread per call (each with its own database
    object), wait until all are blocked behind the lock, release it, and return their results."""
    lock = row_lock()
    lock.execute("SELECT 1 FROM jobs WHERE job_id = %s::uuid FOR UPDATE", job_id)
    results = [None] * len(calls)
    errors = []

    def runner(i, call):
        try:
            results[i] = call()
        except Exception as err:  # reported below
            errors.append(err)

    threads = [threading.Thread(target=runner, args=(i, c)) for i, c in enumerate(calls)]
    for t in threads:
        t.start()
    lock.wait_for_blocked(len(calls))
    lock.release()
    for t in threads:
        t.join(10)
    assert not any(t.is_alive() for t in threads), "a call never finished"
    assert errors == []
    return results


def test_concurrent_settle_and_refund_end_in_exactly_one_outcome(db, db_dsn, pg, row_lock):
    job_id = claimed(db)
    results = run_blocked(
        row_lock,
        job_id,
        lambda: billing.settle_success(PostgresDatabase(db_dsn), job_id),
        lambda: billing.issue_refund(PostgresDatabase(db_dsn), job_id, "stalled", attempt=1),
    )
    assert sorted(results) == [False, True]
    status = pg.job(job_id)["status"]
    assert status == ("done" if results[0] else "failed")
    assert money(pg) == ((410, 0) if results[0] else (500, 0))


def test_concurrent_claims_give_exactly_one_attempt(db, db_dsn, pg, row_lock):
    make_user(db)
    job_id, _ = reserve(db)
    results = run_blocked(
        row_lock,
        job_id,
        lambda: billing.claim(PostgresDatabase(db_dsn), job_id),
        lambda: billing.claim(PostgresDatabase(db_dsn), job_id),
    )
    assert sorted(results, key=lambda r: r is None) == [1, None]
    assert pg.job(job_id)["attempt"] == 1
