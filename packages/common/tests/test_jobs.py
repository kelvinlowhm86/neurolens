import threading
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from neurolens_common import jobs, ledger, reaper_sql
from neurolens_common.jobs import JobStatus

from neurolens_common.testing import assert_ledger_consistent

NOW = datetime.now(timezone.utc)


def _status(conn, job_id):
    return conn.execute("SELECT status FROM jobs WHERE id = %s", (job_id,)).fetchone()["status"]


def test_two_concurrent_claims_one_winner(db_url, conn, make_user, make_job):
    uid = make_user(100)
    job = make_job(uid)
    ledger.reserve(conn, job, 30)
    wins = []
    barrier = threading.Barrier(2)

    def claim(worker_id):
        with psycopg.connect(db_url, autocommit=True, row_factory=dict_row) as c:
            barrier.wait()
            if jobs.claim_job(c, job, worker_id, NOW):
                wins.append(worker_id)

    threads = [threading.Thread(target=claim, args=(w,)) for w in ("i-aaa", "i-bbb")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1


def test_claim_refused_while_heartbeat_fresh_then_allowed_when_stale(conn, make_user, make_job):
    uid = make_user(100)
    job = make_job(uid)
    assert jobs.claim_job(conn, job, "i-aaa", NOW)
    assert jobs.claim_job(conn, job, "i-bbb", NOW) is None       # first worker alive
    conn.execute("UPDATE jobs SET heartbeat_at = now() - interval '5 minutes' WHERE id = %s", (job,))
    row = jobs.claim_job(conn, job, "i-bbb", NOW)                  # first worker died
    assert row and row["attempts"] == 2
    assert jobs.heartbeat(conn, job, "i-aaa") is False             # old owner is locked out


def test_happy_path_success_charges_actual(conn, make_user, make_job):
    uid = make_user(100)
    job = make_job(uid, est=30)
    ledger.reserve(conn, job, 30)
    jobs.mark_uploaded(conn, job, uid)
    assert _status(conn, job) == JobStatus.UPLOADED
    assert jobs.claim_job(conn, job, "i-aaa", NOW)
    assert jobs.set_stage(conn, job, "i-aaa", "PASS_FULL", 20, finished_stage="VALIDATING")
    ledger.adjust(conn, job, 28)
    ok = jobs.complete_job(conn, job, "i-aaa", credits=28, result_key="results/x.json",
                           summary={"peak_t": 3}, gpu_device="L40S", peak_vram_gb=31.2, cold_start=True)
    assert ok
    j = jobs.get_job(conn, job)
    assert j["status"] == JobStatus.SUCCEEDED and j["credits_captured"] == 28 and j["validated_at"]
    assert ledger.balance(conn, uid) == {"credits_available": 72, "credits_reserved": 0}
    assert_ledger_consistent(conn, uid)


def test_complete_by_non_owner_changes_nothing(conn, make_user, make_job):
    uid = make_user(100)
    job = make_job(uid)
    ledger.reserve(conn, job, 30)
    jobs.claim_job(conn, job, "i-aaa", NOW)
    ok = jobs.complete_job(conn, job, "i-zzz", credits=30, result_key="r", summary={},
                           gpu_device=None, peak_vram_gb=None, cold_start=False)
    assert ok is False
    assert ledger.balance(conn, uid) == {"credits_available": 70, "credits_reserved": 30}


def test_spot_requeue_keeps_reservation(conn, make_user, make_job):
    uid = make_user(100)
    job = make_job(uid)
    ledger.reserve(conn, job, 30)
    jobs.claim_job(conn, job, "i-aaa", NOW)
    assert jobs.requeue_job(conn, job, "i-aaa")
    assert _status(conn, job) == JobStatus.UPLOADED
    assert jobs.claim_job(conn, job, "i-bbb", NOW)                 # next worker takes it at once
    assert ledger.balance(conn, uid)["credits_reserved"] == 30


def test_close_job_refunds_once(conn, make_user, make_job):
    uid = make_user(100)
    job = make_job(uid)
    ledger.reserve(conn, job, 30)
    jobs.claim_job(conn, job, "i-aaa", NOW)
    assert jobs.close_job(conn, job, JobStatus.FAILED, "TOO_LONG", "Video is 140 s; max 120 s")
    assert jobs.close_job(conn, job, JobStatus.FAILED, "TOO_LONG") is False
    assert ledger.balance(conn, uid) == {"credits_available": 100, "credits_reserved": 0}
    assert jobs.claim_job(conn, job, "i-bbb", NOW) is None          # final jobs can't be claimed
    assert_ledger_consistent(conn, uid)


def test_cancel_only_before_upload(conn, make_user, make_job):
    uid = make_user(100)
    a, b = make_job(uid), make_job(uid)
    ledger.reserve(conn, a, 30)
    ledger.reserve(conn, b, 30)
    assert jobs.cancel_job(conn, a, uid)
    jobs.claim_job(conn, b, "i-aaa", NOW)
    assert jobs.cancel_job(conn, b, uid) is False
    assert ledger.balance(conn, uid) == {"credits_available": 70, "credits_reserved": 30}


def test_reaper_rules(conn, make_user, make_job):
    uid = make_user(200)
    stale_upload, stuck, waiting, fresh = (make_job(uid) for _ in range(4))
    for j in (stale_upload, stuck, waiting, fresh):
        ledger.reserve(conn, j, 30)
    conn.execute("UPDATE jobs SET created_at = now() - interval '31 minutes' WHERE id = %s", (stale_upload,))
    jobs.claim_job(conn, stuck, "i-aaa", NOW)
    conn.execute("""UPDATE jobs SET attempts = 2, heartbeat_at = now() - interval '11 minutes'
                     WHERE id = %s""", (stuck,))
    jobs.mark_uploaded(conn, waiting, uid)
    conn.execute("UPDATE jobs SET created_at = now() - interval '61 minutes' WHERE id = %s", (waiting,))

    closed = reaper_sql.run_rules(conn)
    assert closed["expire_uploads"] == 1 and closed["stuck_processing"] == 1 and closed["sla_breach"] == 1
    assert _status(conn, stale_upload) == JobStatus.EXPIRED
    assert _status(conn, stuck) == JobStatus.FAILED
    assert _status(conn, waiting) == JobStatus.FAILED
    assert _status(conn, fresh) == JobStatus.AWAITING_UPLOAD
    assert reaper_sql.run_rules(conn) == {k: 0 for k in reaper_sql.REAPER_RULES}   # idempotent
    assert ledger.balance(conn, uid) == {"credits_available": 170, "credits_reserved": 30}
    assert_ledger_consistent(conn, uid)


def test_dlq_refund_idempotent(conn, make_user, make_job):
    uid = make_user(100)
    job = make_job(uid)
    ledger.reserve(conn, job, 30)
    jobs.claim_job(conn, job, "i-aaa", NOW)
    assert reaper_sql.refund_from_dlq(conn, str(job)) is True
    assert reaper_sql.refund_from_dlq(conn, str(job)) is False
    assert ledger.balance(conn, uid) == {"credits_available": 100, "credits_reserved": 0}


def test_as_psycopg_leaves_casts():
    assert reaper_sql.as_psycopg("SELECT CAST(:job_id AS uuid), x::text") == \
        "SELECT CAST(%(job_id)s AS uuid), x::text"
