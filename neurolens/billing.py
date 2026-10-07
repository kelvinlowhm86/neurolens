"""Credit billing (M3a §4): reserve at upload, verify after measuring, capture on success, and
refund on every failure path.

Every function takes a `Database` (neurolens.db) first and does its work in one transaction.
Row locks are taken on the `jobs` row first, then the `balances` row, so two functions can never
deadlock each other. Every balance change writes its ledger row in the same transaction.

The attempt number returned by `claim` is a worker's proof that it still holds a job: worker-side
calls act only while the job is `processing` with that attempt, and otherwise change nothing and
report a lost claim. Only `settle_success` and `issue_refund` end a job; both lock the job row
and refuse a terminal job, so a job is charged or refunded exactly once, never both.

Pure Python (the Lambdas import this): no numpy, no psycopg.
"""

import json
from datetime import UTC, datetime

from neurolens.pricing import estimate_cost_cents
from neurolens.storage import TIME_FORMAT

QUEUED, PROCESSING, DONE, FAILED = "queued", "processing", "done", "failed"
TERMINAL = (DONE, FAILED)
LOST_CLAIM = "lost_claim"
OK = "ok"


class InsufficientCredit(Exception):
    def __init__(self, available_cents, required_cents):
        super().__init__(f"needs {required_cents} cents, {available_cents} available")
        self.available_cents = available_cents
        self.required_cents = required_cents


# ---------------------------------------------------------------- helpers (inside a transaction)


def _lock_job(tx, job_id):
    rows = tx.execute(
        "SELECT user_id, status, attempt, reserved_cents FROM jobs "
        "WHERE job_id = CAST(:job_id AS uuid) FOR UPDATE",
        {"job_id": job_id},
    )
    return rows[0] if rows else None


def _holds_claim(job, attempt):
    return job is not None and job["status"] == PROCESSING and job["attempt"] == attempt


def _move(tx, user_id, job_id, kind, available_delta, reserved_delta):
    """Change the balance and write the matching ledger row (the ledger invariant, §3c)."""
    tx.execute(
        "UPDATE balances SET available_cents = available_cents + :available, "
        "reserved_cents = reserved_cents + :reserved WHERE user_id = :user_id",
        {"user_id": user_id, "available": available_delta, "reserved": reserved_delta},
    )
    tx.execute(
        "INSERT INTO ledger (user_id, job_id, kind, available_delta_cents, reserved_delta_cents) "
        "VALUES (:user_id, CAST(:job_id AS uuid), :kind, :available, :reserved)",
        {
            "user_id": user_id,
            "job_id": job_id,
            "kind": kind,
            "available": available_delta,
            "reserved": reserved_delta,
        },
    )


def _refund(tx, job_id, job, reason, message):
    """Return the job's reservation and fail it. The caller holds the job's row lock and has
    checked that the job is not terminal."""
    cents = job["reserved_cents"]
    _move(tx, job["user_id"], job_id, "refund", cents, -cents)
    tx.execute(
        "INSERT INTO refunds (job_id, reason) VALUES (CAST(:job_id AS uuid), :reason)",
        {"job_id": job_id, "reason": reason},
    )
    tx.execute(
        "UPDATE jobs SET status = :failed, error_code = :reason, error_message = :message, "
        "updated_at = now() WHERE job_id = CAST(:job_id AS uuid)",
        {"failed": FAILED, "reason": reason, "message": message, "job_id": job_id},
    )


# ---------------------------------------------------------------- users and balances


def ensure_user(db, user_id, email, starter_cents):
    """Create the user with the starter credit, once. Two simultaneous first calls both succeed
    and grant it once; a later call changes nothing (not even the email)."""
    with db.transaction() as tx:
        created = tx.execute(
            "INSERT INTO users (user_id, email) VALUES (:user_id, :email) "
            "ON CONFLICT DO NOTHING RETURNING user_id",
            {"user_id": user_id, "email": email},
        )
        if not created:
            return
        tx.execute(
            "INSERT INTO balances (user_id, available_cents, reserved_cents) "
            "VALUES (:user_id, 0, 0)",
            {"user_id": user_id},
        )
        _move(tx, user_id, None, "starter", starter_cents, 0)


def get_balance(db, user_id):
    with db.transaction() as tx:
        rows = tx.execute(
            "SELECT available_cents, reserved_cents FROM balances WHERE user_id = :user_id",
            {"user_id": user_id},
        )
    if not rows:
        raise LookupError(f"no balance for user {user_id!r}: call ensure_user first")
    return rows[0]


# ---------------------------------------------------------------- Stage 1


def reserve(db, user_id, job_id, object_key, filename, client_duration_ms):
    """Move the estimated price from available to reserved and create the queued job.
    Raises InsufficientCredit (changing nothing) if the available credit is lower."""
    price = estimate_cost_cents(client_duration_ms / 1000)
    with db.transaction() as tx:
        rows = tx.execute(
            "SELECT available_cents FROM balances WHERE user_id = :user_id FOR UPDATE",
            {"user_id": user_id},
        )
        if not rows:
            raise LookupError(f"no balance for user {user_id!r}: call ensure_user first")
        available = rows[0]["available_cents"]
        if available < price:
            raise InsufficientCredit(available, price)
        tx.execute(
            "INSERT INTO jobs (job_id, user_id, object_key, filename, status, "
            "client_duration_ms, reserved_cents) VALUES (CAST(:job_id AS uuid), :user_id, "
            ":object_key, :filename, :queued, :client_duration_ms, :price)",
            {
                "job_id": job_id,
                "user_id": user_id,
                "object_key": object_key,
                "filename": filename,
                "queued": QUEUED,
                "client_duration_ms": client_duration_ms,
                "price": price,
            },
        )
        _move(tx, user_id, job_id, "reserve", -price, price)
    return price


# ---------------------------------------------------------------- claiming and progress


def claim(db, job_id, stale_after_s=90):
    """Take a queued job, or a processing one whose worker stopped heartbeating. Returns the new
    attempt, or None if the job is unknown, terminal, or held by a live worker."""
    with db.transaction() as tx:
        rows = tx.execute(
            "UPDATE jobs SET status = :processing, attempt = attempt + 1, stage = NULL, "
            "stages = jsonb_build_array(), updated_at = now() "
            "WHERE job_id = CAST(:job_id AS uuid) AND (status = :queued OR "
            "(status = :processing AND updated_at < now() - make_interval(secs => :stale))) "
            "RETURNING attempt",
            {"job_id": job_id, "processing": PROCESSING, "queued": QUEUED, "stale": stale_after_s},
        )
    return rows[0]["attempt"] if rows else None


def job_state(db, job_id):
    """{"status", "updated_at", "attempt"}, or None for an unknown job."""
    with db.transaction() as tx:
        rows = tx.execute(
            "SELECT status, updated_at, attempt FROM jobs WHERE job_id = CAST(:job_id AS uuid)",
            {"job_id": job_id},
        )
    return rows[0] if rows else None


def _update_held(db, job_id, attempt, assignments, params):
    """One guarded UPDATE: True if this attempt still held the claim and the row changed."""
    with db.transaction() as tx:
        rows = tx.execute(
            f"UPDATE jobs SET {assignments} WHERE job_id = CAST(:job_id AS uuid) "
            "AND status = :processing AND attempt = :attempt RETURNING attempt",
            {**params, "job_id": job_id, "attempt": attempt, "processing": PROCESSING},
        )
    return bool(rows)


def touch(db, job_id, attempt):
    """The heartbeat's sign of life. False means the claim was lost."""
    return _update_held(db, job_id, attempt, "updated_at = now()", {})


def set_stage(db, job_id, attempt, stage, now=None):
    """Set `stage` and append {"stage", "at"} to `stages` (UTC, M2b §4's format)."""
    at = (now or datetime.now(UTC)).astimezone(UTC).strftime(TIME_FORMAT)
    return _update_held(
        db,
        job_id,
        attempt,
        "stage = :stage, stages = stages || CAST(:entry AS jsonb)",
        {"stage": stage, "entry": json.dumps([{"stage": stage, "at": at}])},
    )


def release_for_retry(db, job_id, attempt, error_message=None):
    """Hand the job back to the queue (failure or shutdown) so the next worker can claim it at
    once. Money is untouched. False means the claim was lost."""
    return _update_held(
        db,
        job_id,
        attempt,
        "status = :queued, updated_at = now(), error_message = :message",
        {"queued": QUEUED, "message": error_message},
    )


# ---------------------------------------------------------------- Stage 2


def verify(db, job_id, attempt, verified_duration_ms, max_duration_s):
    """Price the job by its measured duration, before any inference. Returns "ok", "lost_claim"
    (nothing changed), or the refund reason it applied."""
    with db.transaction() as tx:
        job = _lock_job(tx, job_id)
        if not _holds_claim(job, attempt):
            return LOST_CLAIM
        tx.execute(
            "UPDATE jobs SET verified_duration_ms = :ms WHERE job_id = CAST(:job_id AS uuid)",
            {"ms": verified_duration_ms, "job_id": job_id},
        )
        # The length limit is a product rule, checked before any credit logic, whatever the
        # balance (settings.max_duration).
        if verified_duration_ms > max_duration_s * 1000:
            reason = "duration_exceeds_max_verified"
            _refund(tx, job_id, job, reason, None)
            return reason
        price = estimate_cost_cents(verified_duration_ms / 1000)
        difference = price - job["reserved_cents"]
        if difference == 0:
            return OK
        if difference > 0:
            [balance] = tx.execute(
                "SELECT available_cents FROM balances WHERE user_id = :user_id FOR UPDATE",
                {"user_id": job["user_id"]},
            )
            if balance["available_cents"] < difference:
                reason = "insufficient_credit_for_actual_duration"
                _refund(tx, job_id, job, reason, None)
                return reason
        _move(tx, job["user_id"], job_id, "adjust", -difference, difference)
        tx.execute(
            "UPDATE jobs SET reserved_cents = :price WHERE job_id = CAST(:job_id AS uuid)",
            {"price": price, "job_id": job_id},
        )
    return OK


# ---------------------------------------------------------------- Stage 3 and refunds


def settle_success(db, job_id):
    """Charge a job whose result exists, whoever finished it (no attempt guard). False, changing
    nothing, for an unknown job or one already done or failed."""
    with db.transaction() as tx:
        job = _lock_job(tx, job_id)
        if job is None or job["status"] in TERMINAL:
            return False
        cents = job["reserved_cents"]
        _move(tx, job["user_id"], job_id, "capture", 0, -cents)
        tx.execute(
            "UPDATE jobs SET status = :done, captured_cents = :cents, error_code = NULL, "
            "error_message = NULL, updated_at = now() WHERE job_id = CAST(:job_id AS uuid)",
            {"done": DONE, "cents": cents, "job_id": job_id},
        )
    return True


def issue_refund(
    db, job_id, reason, message=None, *, attempt=None, queued_before_s=None, processing_stale_s=None
):
    """Refund a job if at least one stated condition holds, re-checked under the job's row lock:

    - attempt: the job is processing under this attempt (the worker holding the claim);
    - queued_before_s: it is queued and was last updated at least that long ago (0: any queued);
    - processing_stale_s: it is processing and was last updated at least that long ago.

    Every condition requires a non-terminal status, so a done or failed job is never refunded.
    Returns False, changing nothing, when none holds. No condition at all raises ValueError:
    every caller must state why the refund is safe.
    """
    if attempt is None and queued_before_s is None and processing_stale_s is None:
        raise ValueError("issue_refund needs attempt, queued_before_s or processing_stale_s")
    with db.transaction() as tx:
        rows = tx.execute(
            "SELECT user_id, status, attempt, reserved_cents, "
            "updated_at <= now() - make_interval(secs => :queued_s) AS queued_old, "
            "updated_at <= now() - make_interval(secs => :stale_s) AS processing_old "
            "FROM jobs WHERE job_id = CAST(:job_id AS uuid) FOR UPDATE",
            {
                "job_id": job_id,
                "queued_s": queued_before_s or 0,
                "stale_s": processing_stale_s or 0,
            },
        )
        job = rows[0] if rows else None
        if job is None:
            return False
        allowed = (
            (attempt is not None and _holds_claim(job, attempt))
            or (queued_before_s is not None and job["status"] == QUEUED and job["queued_old"])
            or (
                processing_stale_s is not None
                and job["status"] == PROCESSING
                and job["processing_old"]
            )
        )
        if not allowed:
            return False
        _refund(tx, job_id, job, reason, message)
    return True
