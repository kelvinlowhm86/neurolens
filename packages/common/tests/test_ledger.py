import threading
import uuid

import psycopg
import pytest

from neurolens_common import ledger
from neurolens_common.ledger import InsufficientCredits

from neurolens_common.testing import assert_ledger_consistent


def test_grant_is_idempotent_per_ref(conn, make_user):
    uid = make_user()
    assert ledger.grant(conn, uid, 300, "PURCHASE", "cs_test_1") is True
    assert ledger.grant(conn, uid, 300, "PURCHASE", "cs_test_1") is False   # webhook replay
    assert ledger.balance(conn, uid)["credits_available"] == 300
    assert_ledger_consistent(conn, uid)


def test_reserve_capture_returns_unused(conn, make_user, make_job):
    uid = make_user(100)
    job = make_job(uid)
    ledger.reserve(conn, job, 30)
    assert ledger.balance(conn, uid) == {"credits_available": 70, "credits_reserved": 30}
    assert ledger.capture(conn, job, 28) is True
    assert ledger.balance(conn, uid) == {"credits_available": 72, "credits_reserved": 0}
    assert ledger.capture(conn, job, 28) is False          # second capture changes nothing
    assert ledger.release(conn, job, "late") is False      # nor does a release after capture
    assert ledger.balance(conn, uid)["credits_available"] == 72
    assert_ledger_consistent(conn, uid)


def test_reserve_insufficient(conn, make_user, make_job):
    uid = make_user(10)
    job = make_job(uid)
    with pytest.raises(InsufficientCredits):
        ledger.reserve(conn, job, 30)
    assert ledger.balance(conn, uid) == {"credits_available": 10, "credits_reserved": 0}


def test_adjust_up_and_down(conn, make_user, make_job):
    uid = make_user(100)
    job = make_job(uid)
    ledger.reserve(conn, job, 30)
    ledger.adjust(conn, job, 45)      # real video longer than estimated
    assert ledger.balance(conn, uid) == {"credits_available": 55, "credits_reserved": 45}
    ledger.adjust(conn, job, 20)      # shorter
    assert ledger.balance(conn, uid) == {"credits_available": 80, "credits_reserved": 20}
    assert_ledger_consistent(conn, uid)


def test_adjust_beyond_balance_fails_and_keeps_hold(conn, make_user, make_job):
    uid = make_user(40)
    job = make_job(uid)
    ledger.reserve(conn, job, 30)
    with pytest.raises(InsufficientCredits):
        ledger.adjust(conn, job, 60)
    assert ledger.balance(conn, uid) == {"credits_available": 10, "credits_reserved": 30}


def test_release_twice_is_noop(conn, make_user, make_job):
    uid = make_user(50)
    job = make_job(uid)
    ledger.reserve(conn, job, 30)
    assert ledger.release(conn, job, "DLQ") is True
    assert ledger.release(conn, job, "REAPER") is False
    assert ledger.balance(conn, uid) == {"credits_available": 50, "credits_reserved": 0}
    assert_ledger_consistent(conn, uid)


def test_capture_more_than_held_rejected(conn, make_user, make_job):
    uid = make_user(50)
    job = make_job(uid)
    ledger.reserve(conn, job, 30)
    with pytest.raises(psycopg.Error):
        ledger.capture(conn, job, 31)


def test_concurrent_reserves_never_overdraw(db_url, conn, make_user, make_job):
    """20 parallel reservations of 30 against a 300 balance: exactly 10 succeed."""
    uid = make_user(300)
    job_ids = [make_job(uid) for _ in range(20)]
    results: list[str] = []
    lock = threading.Lock()

    def worker(job_id: uuid.UUID):
        with psycopg.connect(db_url, autocommit=True) as c:
            try:
                ledger.reserve(c, job_id, 30)
                outcome = "ok"
            except InsufficientCredits:
                outcome = "insufficient"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(j,)) for j in job_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("ok") == 10
    assert results.count("insufficient") == 10
    assert ledger.balance(conn, uid) == {"credits_available": 0, "credits_reserved": 300}
    assert_ledger_consistent(conn, uid)
