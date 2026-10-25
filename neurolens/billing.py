"""Credit billing (M3a §4): reserve at upload, verify after measuring, capture on success, and
refund on every failure path.

Every function takes a `Database` (neurolens.db) first and does its work in one transaction.
Row locks are taken on the `balances` row first, then the `jobs` row, so two functions can never
deadlock each other. Every balance change writes its ledger row in the same transaction.

The attempt number returned by `claim` is a worker's proof that it still holds a job: worker-side
calls act only while the job is `processing` with that attempt, and otherwise change nothing and
report a lost claim. Only `settle_success` and `issue_refund` end a job; both lock the job row
and refuse a terminal job, so a job is charged or refunded exactly once, never both.

Pure Python (the Lambdas import this): no numpy, no psycopg.
"""

import json
import logging
import uuid
from datetime import UTC, datetime

from neurolens import storage
from neurolens.pricing import estimate_cost_cents

logger = logging.getLogger("neurolens")

QUEUED, PROCESSING, DONE, FAILED = "queued", "processing", "done", "failed"
TERMINAL = (DONE, FAILED)
LOST_CLAIM = "lost_claim"
OK = "ok"
# A live worker refreshes its claim every <= 50 s; a processing job older than this is dead.
STALE_AFTER_S = 90
# The wording of the research-consent box (M3f §5a). A wording change needs a new value here and
# the new text on the page together; old jobs keep the version they agreed to.
CONSENT_VERSION = "2026-10-25"
# A session row older than this (by its last use) no longer signs anybody in (M3f §6b).
SESSION_DAYS = 30

CURVE_REMOVED_MESSAGES = {
    "stopped": "You stopped sharing this video.",
    "no_curve": "This video's research copy was deleted after 90 days without a curve.",
}


class InsufficientCredit(Exception):
    def __init__(self, available_cents, required_cents):
        super().__init__(f"needs {required_cents} cents, {available_cents} available")
        self.available_cents = available_cents
        self.required_cents = required_cents


class JobActive(Exception):
    """A job is queued or processing, so deleting it (or its account) is refused."""


class TooManyWaiting(Exception):
    """The user already has the maximum number of queued jobs (M3f §2a)."""


class DuplicateCurve(Exception):
    """This exact retention curve is already stored (M3f §5d)."""


class CurveRefused(Exception):
    """A job that cannot take a curve, with the reply the web route gives (M3f §5d)."""

    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ---------------------------------------------------------------- helpers (inside a transaction)


def _lock_balance(tx, user_id):
    rows = tx.execute(
        "SELECT available_cents FROM balances WHERE user_id = :user_id FOR UPDATE",
        {"user_id": user_id},
    )
    return rows[0] if rows else None


def _lock_job_balance(tx, job_id):
    """Lock the job owner's balance before the job row itself (the one lock order)."""
    owners = tx.execute(
        "SELECT user_id FROM jobs WHERE job_id = CAST(:job_id AS uuid)",
        {"job_id": job_id},
    )
    if owners:
        _lock_balance(tx, owners[0]["user_id"])
    return bool(owners)


def _lock_job(tx, job_id):
    _lock_job_balance(tx, job_id)
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
        "UPDATE jobs SET status = :failed, error_code = :reason, error_message = :message, "
        "updated_at = now() WHERE job_id = CAST(:job_id AS uuid)",
        {"failed": FAILED, "reason": reason, "message": message, "job_id": job_id},
    )


# ---------------------------------------------------------------- users and balances


def _create_user(tx, user_id, email, starter_cents):
    """Insert the user with its balance and the starter credit (no ledger row for 0). False,
    changing nothing, when the user ID or the email (any letter case) is already taken: the
    insert waits for a simultaneous one to commit, then does nothing."""
    created = tx.execute(
        "INSERT INTO users (user_id, email) VALUES (:user_id, :email) "
        "ON CONFLICT DO NOTHING RETURNING user_id",
        {"user_id": user_id, "email": email},
    )
    if not created:
        return False
    tx.execute(
        "INSERT INTO balances (user_id, available_cents, reserved_cents) VALUES (:user_id, 0, 0)",
        {"user_id": user_id},
    )
    if starter_cents:
        _move(tx, user_id, None, "starter", starter_cents, 0)
    return True


def _user_by_email(tx, email):
    rows = tx.execute(
        "SELECT user_id FROM users WHERE lower(email) = lower(:email)", {"email": email}
    )
    return rows[0]["user_id"] if rows else None


def _keep_email_current(tx, user_id, email):
    """Store a returning sign-in's verified email (M3b §2c), so an address the user has given up
    no longer links a stranger to their account. Left unchanged when another user holds it."""
    stored = tx.execute("SELECT email FROM users WHERE user_id = :user_id", {"user_id": user_id})
    if stored[0]["email"].lower() == email.lower():
        return
    # One conditional statement: a unique-violation error would abort the whole transaction.
    updated = tx.execute(
        "UPDATE users SET email = :email WHERE user_id = :user_id AND NOT EXISTS "
        "(SELECT 1 FROM users WHERE lower(email) = lower(:email)) RETURNING user_id",
        {"user_id": user_id, "email": email},
    )
    if not updated:
        logger.warning(
            f"user {user_id} signed in with an email another user holds: stored email kept"
        )


def ensure_user(db, user_id, email, starter_cents):
    """Create the user with the starter credit, once (dev mode's fixed user, M3b §2b). Two
    simultaneous first calls both succeed and grant it once; a later call changes nothing (not
    even the email)."""
    with db.transaction() as tx:
        _create_user(tx, user_id, email, starter_cents)


def sign_in(db, issuer, subject, email, starter_cents):
    """The user for a verified sign-in (M3b §2c), in one transaction: the sign-in's own
    `(issuer, subject)` first; else the user with this email (any letter case), linked to it;
    else a new user with a random UUID and the starter credit. Every insert uses
    ON CONFLICT DO NOTHING and re-reads the winner, because a caught unique-violation would
    abort the transaction (on Postgres and the Data API alike)."""
    key = {"issuer": issuer, "subject": subject}
    find_identity = "SELECT user_id FROM identities WHERE issuer = :issuer AND subject = :subject"
    with db.transaction() as tx:
        rows = tx.execute(find_identity, key)
        if rows:
            user_id = rows[0]["user_id"]
            _keep_email_current(tx, user_id, email)
            return user_id
        user_id = _user_by_email(tx, email)
        if user_id is None:
            user_id = str(uuid.uuid4())
            if not _create_user(tx, user_id, email, starter_cents):
                user_id = _user_by_email(tx, email)
        linked = tx.execute(
            "INSERT INTO identities (issuer, subject, user_id) "
            "VALUES (:issuer, :subject, :user_id) ON CONFLICT DO NOTHING RETURNING user_id",
            {**key, "user_id": user_id},
        )
        return linked[0]["user_id"] if linked else tx.execute(find_identity, key)[0]["user_id"]


def get_email(db, user_id):
    """The user's email, or None for an unknown user."""
    with db.transaction() as tx:
        rows = tx.execute("SELECT email FROM users WHERE user_id = :user_id", {"user_id": user_id})
    return rows[0]["email"] if rows else None


def user_by_email(db, email):
    """The user ID for this email (any letter case), or None."""
    with db.transaction() as tx:
        return _user_by_email(tx, email)


def grant_credit(db, email, cents):
    """Add credit to the user with this email (any letter case), with a `grant` ledger row
    (M3b §7a). Returns the user ID."""
    if not isinstance(cents, int) or isinstance(cents, bool) or cents <= 0:
        raise ValueError(f"cents must be a positive whole number, not {cents!r}")
    with db.transaction() as tx:
        user_id = _user_by_email(tx, email)
        if user_id is None:
            raise LookupError(f"no user with email {email!r}")
        _move(tx, user_id, None, "grant", cents, 0)
    return user_id


def list_jobs(db, user_id, limit):
    """The user's jobs, newest first, at most `limit` (M3b §4a, M3f §3, §5d).

    Deleted and cancelled jobs are left out. `claimed_at` is UTC text; `cancellable` marks a
    queued job the dispatcher has not priced yet (M3e §8). For a shared job, `sharing` is true
    while its research copy is present, `curve_count` is its stored curves, and
    `curve_reward_cents` is what a valid curve would return now (0 when it cannot).
    """
    with db.transaction() as tx:
        rows = tx.execute(
            "SELECT j.job_id, j.filename, j.status, j.created_at, j.verified_duration_ms, "
            "j.captured_cents, j.error_code, j.research_consent, j.claimed_at, "
            "(j.status = :queued AND j.verified_duration_ms IS NULL) AS cancellable, "
            "(j.research_consent AND j.dataset_removed_at IS NULL) AS sharing, "
            "(SELECT count(*) FROM retention_snapshots s "
            "WHERE s.job_id = j.job_id) AS curve_count, "
            "CASE WHEN j.status = :done AND j.research_consent "
            "AND j.dataset_removed_at IS NULL AND j.captured_cents > 0 "
            "AND j.curve_rewarded_at IS NULL AND NOT EXISTS "
            "(SELECT 1 FROM jobs other WHERE other.video_sha256 = j.video_sha256 "
            "AND other.video_sha256 IS NOT NULL AND other.curve_rewarded_at IS NOT NULL) "
            "THEN j.captured_cents ELSE 0 END AS curve_reward_cents "
            "FROM jobs j WHERE j.user_id = :user_id AND j.deleted_at IS NULL "
            "AND (j.error_code IS NULL OR j.error_code <> :cancelled) "
            "ORDER BY j.created_at DESC LIMIT :limit",
            {
                "user_id": user_id,
                "limit": limit,
                "done": DONE,
                "queued": QUEUED,
                "cancelled": "cancelled",
            },
        )
    for row in rows:
        if row["claimed_at"] is not None:
            row["claimed_at"] = row["claimed_at"].astimezone(UTC).strftime(storage.TIME_FORMAT)
    return rows


def delete_job(db, user_id, job_id):
    """Mark the user's job deleted under its row lock, keeping its row and ledger (M3c §4).

    This is the mark half of job deletion (M3f §5c): it happens before the files are purged, so
    no curve can be added while the purge runs. False, changing nothing, when the job is not the
    user's, already deleted, or still queued or processing.
    """
    with db.transaction() as tx:
        rows = tx.execute(
            "UPDATE jobs SET deleted_at = now(), filename = NULL "
            "WHERE job_id = CAST(:job_id AS uuid) AND user_id = :user_id "
            "AND deleted_at IS NULL AND status NOT IN (:queued, :processing) RETURNING job_id",
            {
                "job_id": job_id,
                "user_id": user_id,
                "queued": QUEUED,
                "processing": PROCESSING,
            },
        )
    return bool(rows)


def purge_job_files(db, s3, bucket, job_id):
    """Delete a marked job's files, then record that they are gone (M3f §5c).

    Both research prefixes always go. A deleted job's own files (upload, result, brain,
    thumbnail) go too; a copy that was only removed keeps the user's own result.
    """
    with db.transaction() as tx:
        rows = tx.execute(
            "SELECT object_key, deleted_at FROM jobs WHERE job_id = CAST(:job_id AS uuid)",
            {"job_id": job_id},
        )
    job = rows[0] if rows else None
    storage.delete_job_prefixes(s3, bucket, job_id)
    if job is not None and job["deleted_at"] is not None:
        keys = storage.job_keys(job_id, job["object_key"])
        if not storage.delete_keys(s3, bucket, keys):
            raise RuntimeError(f"{job_id}: S3 reported a failed delete")
    mark_dataset_purged(db, job_id)


def mark_dataset_removed(db, job_id, reason, *, user_id=None, require_no_snapshot=False):
    """Mark a job's shared copy removed under its row lock, if it is still eligible (M3f §5c).

    One guarded update: the job must be done, consented, not already removed and not deleted.
    With `user_id` it must also be that user's, and with `require_no_snapshot` it must still have
    no curve (the reaper's 90-day rule). False, changing nothing, when it no longer qualifies.
    """
    clauses = [
        "job_id = CAST(:job_id AS uuid)",
        "status = :done",
        "research_consent = TRUE",
        "dataset_removed_at IS NULL",
        "deleted_at IS NULL",
    ]
    params = {"job_id": job_id, "reason": reason, "done": DONE}
    if user_id is not None:
        clauses.append("user_id = :user_id")
        params["user_id"] = user_id
    if require_no_snapshot:
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM retention_snapshots s WHERE s.job_id = jobs.job_id)"
        )
    with db.transaction() as tx:
        rows = tx.execute(
            "UPDATE jobs SET dataset_removed_at = now(), dataset_removed_reason = :reason "
            f"WHERE {' AND '.join(clauses)} RETURNING job_id",
            params,
        )
    return bool(rows)


def mark_dataset_purged(db, job_id):
    """Record that a marked copy's files are gone (M3f §5c). False if it was not marked."""
    with db.transaction() as tx:
        rows = tx.execute(
            "UPDATE jobs SET dataset_purged_at = now() "
            "WHERE job_id = CAST(:job_id AS uuid) AND dataset_purged_at IS NULL "
            "AND (dataset_removed_at IS NOT NULL OR deleted_at IS NOT NULL) RETURNING job_id",
            {"job_id": job_id},
        )
    return bool(rows)


def curve_exists(db, curve_sha256):
    """True when any job or account already stored this exact curve (M3f §5d)."""
    with db.transaction() as tx:
        rows = tx.execute(
            "SELECT 1 AS present FROM retention_snapshots WHERE curve_sha256 = :sha LIMIT 1",
            {"sha": curve_sha256},
        )
    return bool(rows)


def delete_account(db, user_id):
    """Delete all of a user's rows and return the deleted `(issuer, subject)` identities
    (M3c §5). Refuses with JobActive while any of their jobs is queued or processing.

    Lock order: every caller takes the balance row before job rows. Here the balance row is
    locked first, job statuses are read without locking, and the rows are deleted after the
    route has removed the files (M3f §5c), so no row is left to retry from.
    """
    with db.transaction() as tx:
        locked = tx.execute(
            "SELECT user_id FROM balances WHERE user_id = :user_id FOR UPDATE",
            {"user_id": user_id},
        )
        if not locked:
            raise LookupError(f"no balance for user {user_id!r}: call ensure_user first")
        active = tx.execute(
            "SELECT 1 AS active FROM jobs WHERE user_id = :user_id AND status IN "
            "(:queued, :processing) LIMIT 1",
            {"user_id": user_id, "queued": QUEUED, "processing": PROCESSING},
        )
        if active:
            raise JobActive(f"user {user_id!r} has an active job")
        tx.execute("DELETE FROM sessions WHERE user_id = :user_id", {"user_id": user_id})
        tx.execute("DELETE FROM ledger WHERE user_id = :user_id", {"user_id": user_id})
        tx.execute("DELETE FROM jobs WHERE user_id = :user_id", {"user_id": user_id})
        tx.execute("DELETE FROM balances WHERE user_id = :user_id", {"user_id": user_id})
        identities = tx.execute(
            "DELETE FROM identities WHERE user_id = :user_id RETURNING issuer, subject",
            {"user_id": user_id},
        )
        tx.execute("DELETE FROM users WHERE user_id = :user_id", {"user_id": user_id})
    return [(row["issuer"], row["subject"]) for row in identities]


def get_balance(db, user_id):
    with db.transaction() as tx:
        rows = tx.execute(
            "SELECT available_cents, reserved_cents FROM balances WHERE user_id = :user_id",
            {"user_id": user_id},
        )
    if not rows:
        raise LookupError(f"no balance for user {user_id!r}: call ensure_user first")
    return rows[0]


def record_video_hash(db, job_id, sha256):
    """Store the SHA-256 of the video, so one video can be rewarded once (M3f §5b, §5d)."""
    with db.transaction() as tx:
        rows = tx.execute(
            "UPDATE jobs SET video_sha256 = :sha WHERE job_id = CAST(:job_id AS uuid) "
            "RETURNING job_id",
            {"job_id": job_id, "sha": sha256},
        )
    return bool(rows)


# ---------------------------------------------------------------- Stage 1


def reserve(
    db,
    user_id,
    job_id,
    object_key,
    filename,
    client_duration_ms,
    research_consent=False,
    *,
    max_waiting_jobs=None,
):
    """Move the estimated price from available to reserved and create the queued job.

    Raises InsufficientCredit (changing nothing) if the available credit is lower. With consent,
    the current CONSENT_VERSION and the time are stored too (M3f §5a). `max_waiting_jobs` refuses
    with TooManyWaiting when the user already has that many queued jobs (M3f §2a); the balance
    row lock makes the count and the new job one decision per user.
    """
    price = estimate_cost_cents(client_duration_ms / 1000)
    consent_version = CONSENT_VERSION if research_consent else None
    with db.transaction() as tx:
        rows = tx.execute(
            "SELECT available_cents FROM balances WHERE user_id = :user_id FOR UPDATE",
            {"user_id": user_id},
        )
        if not rows:
            raise LookupError(f"no balance for user {user_id!r}: call ensure_user first")
        if max_waiting_jobs is not None:
            waiting = tx.execute(
                "SELECT count(*) AS n FROM jobs WHERE user_id = :user_id AND status = :queued",
                {"user_id": user_id, "queued": QUEUED},
            )[0]["n"]
            if waiting >= max_waiting_jobs:
                raise TooManyWaiting(f"user {user_id!r} already has {waiting} queued jobs")
        available = rows[0]["available_cents"]
        if available < price:
            raise InsufficientCredit(available, price)
        tx.execute(
            "INSERT INTO jobs (job_id, user_id, object_key, filename, status, "
            "client_duration_ms, reserved_cents, research_consent, consent_version, consented_at) "
            "VALUES (CAST(:job_id AS uuid), :user_id, :object_key, :filename, :queued, "
            ":client_duration_ms, :price, :research_consent, :consent_version, "
            "CASE WHEN :research_consent THEN now() ELSE NULL END)",
            {
                "job_id": job_id,
                "user_id": user_id,
                "object_key": object_key,
                "filename": filename,
                "queued": QUEUED,
                "client_duration_ms": client_duration_ms,
                "price": price,
                "research_consent": research_consent,
                "consent_version": consent_version,
            },
        )
        _move(tx, user_id, job_id, "reserve", -price, price)
    return price


# ---------------------------------------------------------------- claiming and progress


def claim(db, job_id):
    """Take a queued job, or a processing one whose worker stopped heartbeating. Returns the new
    attempt, or None if the job is unknown, terminal, or held by a live worker."""
    with db.transaction() as tx:
        rows = tx.execute(
            "UPDATE jobs SET status = :processing, attempt = attempt + 1, stage = NULL, "
            "stages = jsonb_build_array(), claimed_at = now(), updated_at = now() "
            "WHERE job_id = CAST(:job_id AS uuid) AND (status = :queued OR "
            "(status = :processing AND updated_at < now() - make_interval(secs => :stale))) "
            "RETURNING attempt",
            {
                "job_id": job_id,
                "processing": PROCESSING,
                "queued": QUEUED,
                "stale": STALE_AFTER_S,
            },
        )
    return rows[0]["attempt"] if rows else None


def job_state(db, job_id):
    """The job's status, timing, claim and research fields plus `object_key` and
    `verified_duration_ms` (M3e §3a), or None for an unknown job. Deliberately sees deleted jobs
    too (M3c §4)."""
    with db.transaction() as tx:
        rows = tx.execute(
            "SELECT status, updated_at, attempt, research_consent, object_key, "
            "verified_duration_ms FROM jobs WHERE job_id = CAST(:job_id AS uuid)",
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
    at = (now or datetime.now(UTC)).astimezone(UTC).strftime(storage.TIME_FORMAT)
    return _update_held(
        db,
        job_id,
        attempt,
        "stage = :stage, stages = stages || CAST(:entry AS jsonb)",
        {"stage": stage, "entry": json.dumps([{"stage": stage, "at": at}])},
    )


# ---------------------------------------------------------------- Stage 2


def verify_queued(db, job_id, verified_duration_ms, max_duration_s):
    """Price a queued job by its measured duration, before any GPU runs (M3e §3a). Returns "ok",
    "not_queued" (nothing changed), or the refund reason it applied."""
    with db.transaction() as tx:
        job = _lock_job(tx, job_id)
        if job is None or job["status"] != QUEUED:
            return "not_queued"
        # The length limit is a product rule, checked before any credit logic, whatever the
        # balance (settings.max_duration). Also before recording the duration: a crafted file
        # can claim more hours than the INTEGER column holds.
        if verified_duration_ms > max_duration_s * 1000:
            reason = "duration_exceeds_max_verified"
            _refund(tx, job_id, job, reason, None)
            return reason
        tx.execute(
            "UPDATE jobs SET verified_duration_ms = :ms WHERE job_id = CAST(:job_id AS uuid)",
            {"ms": verified_duration_ms, "job_id": job_id},
        )
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
    db,
    job_id,
    reason,
    message=None,
    *,
    attempt=None,
    queued=False,
    processing_stale_s=None,
    undispatched_only=False,
):
    """Refund a job if at least one stated condition holds, re-checked under the job's row lock:

    - attempt: the job is processing under this attempt (the worker holding the claim);
    - queued: the job is queued (any age);
    - processing_stale_s: it is processing and was last updated at least that long ago;
    - undispatched_only: also require that the dispatcher has not priced it yet
      (`verified_duration_ms IS NULL`), so Cancel and `verify_queued` have one winner.

    Every condition requires a non-terminal status, so a done or failed job is never refunded.
    Returns False, changing nothing, when none holds. No condition at all raises ValueError:
    every caller must state why the refund is safe.
    """
    if attempt is None and not queued and processing_stale_s is None:
        raise ValueError("issue_refund needs attempt, queued or processing_stale_s")
    with db.transaction() as tx:
        _lock_job_balance(tx, job_id)
        rows = tx.execute(
            "SELECT user_id, status, attempt, reserved_cents, verified_duration_ms, "
            "updated_at <= now() - make_interval(secs => :stale_s) AS processing_old "
            "FROM jobs WHERE job_id = CAST(:job_id AS uuid) FOR UPDATE",
            {"job_id": job_id, "stale_s": processing_stale_s or 0},
        )
        job = rows[0] if rows else None
        if job is None:
            return False
        allowed = (
            (attempt is not None and _holds_claim(job, attempt))
            or (queued and job["status"] == QUEUED)
            or (
                processing_stale_s is not None
                and job["status"] == PROCESSING
                and job["processing_old"]
            )
        )
        if undispatched_only:
            allowed = allowed and job["verified_duration_ms"] is None
        if not allowed:
            return False
        _refund(tx, job_id, job, reason, message)
    return True


# ---------------------------------------------------------------- retention curves


def _text_array(values):
    """A PostgreSQL text[] literal for a parameter, as neither backend can bind a Python list."""

    def quoted(value):
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    return "{" + ",".join(quoted(value) for value in values) + "}"


def add_curve(
    db,
    user_id,
    job_id,
    snapshot_id,
    raw_key,
    csv_key,
    curve_sha256,
    points,
    quality_flags,
    views,
    published,
    promoted,
    reward_cap,
    reward_window_days,
):
    """Store one retention snapshot and reward the analysis when all the rules allow it.

    One transaction with the balance row locked before the job row (M3f §5c, §5d): the job must
    be the user's, done, shared, not removed and not deleted. A curve already stored under the
    same hash, on any job or account, raises DuplicateCurve and stores nothing. A valid curve of a
    charged job whose video hash has never been rewarded, by an account that has had credit beyond
    the starter, and below the per-window cap, returns the captured price once.
    """
    with db.transaction() as tx:
        _lock_balance(tx, user_id)
        rows = tx.execute(
            "SELECT user_id, status, research_consent, dataset_removed_at, "
            "dataset_removed_reason, deleted_at, captured_cents, curve_rewarded_at, video_sha256 "
            "FROM jobs WHERE job_id = CAST(:job_id AS uuid) FOR UPDATE",
            {"job_id": job_id},
        )
        job = rows[0] if rows else None
        if job is None or job["user_id"] != user_id or job["deleted_at"] is not None:
            raise CurveRefused("not_found", "No such job.", status=404)
        if job["status"] != DONE:
            raise CurveRefused("job_not_done", "The job is not done yet.")
        if not job["research_consent"]:
            raise CurveRefused("no_consent", "This video was not shared for research.")
        if job["dataset_removed_at"] is not None:
            message = CURVE_REMOVED_MESSAGES.get(
                job["dataset_removed_reason"], CURVE_REMOVED_MESSAGES["no_curve"]
            )
            raise CurveRefused("dataset_removed", message)

        duplicate = tx.execute(
            "SELECT 1 AS present FROM retention_snapshots WHERE curve_sha256 = :sha LIMIT 1",
            {"sha": curve_sha256},
        )
        if duplicate:
            raise DuplicateCurve("this curve was already added")

        reward = 0
        if (
            not quality_flags
            and job["captured_cents"] is not None
            and job["captured_cents"] > 0
            and job["curve_rewarded_at"] is None
        ):
            same_video = tx.execute(
                "SELECT 1 AS present FROM jobs other WHERE other.video_sha256 = :sha "
                "AND other.video_sha256 IS NOT NULL AND other.curve_rewarded_at IS NOT NULL "
                "LIMIT 1",
                {"sha": job["video_sha256"]},
            )
            funded = tx.execute(
                "SELECT 1 AS present FROM ledger WHERE user_id = :user_id "
                "AND (kind = :grant OR kind = :topup) LIMIT 1",
                {"user_id": user_id, "grant": "grant", "topup": "test_topup"},
            )
            recent = tx.execute(
                "SELECT count(*) AS n FROM ledger WHERE user_id = :user_id "
                "AND kind = :kind AND created_at > now() - "
                "make_interval(days => CAST(:days AS integer))",
                {"user_id": user_id, "kind": "curve_reward", "days": reward_window_days},
            )[0]["n"]
            if not same_video and funded and recent < reward_cap:
                reward = job["captured_cents"]

        tx.execute(
            "INSERT INTO retention_snapshots (snapshot_id, job_id, raw_key, csv_key, "
            "curve_sha256, points, quality_flags, views, published, promoted, rewarded_cents) "
            "VALUES (CAST(:snapshot_id AS uuid), CAST(:job_id AS uuid), :raw_key, :csv_key, "
            ":sha, :points, CAST(:flags AS text[]), :views, :published, :promoted, :reward)",
            {
                "snapshot_id": snapshot_id,
                "job_id": job_id,
                "raw_key": raw_key,
                "csv_key": csv_key,
                "sha": curve_sha256,
                "points": len(points),
                "flags": _text_array(quality_flags),
                "views": views,
                "published": published,
                "promoted": promoted,
                "reward": reward,
            },
        )
        tx.execute(
            "UPDATE jobs SET retention_added_at = COALESCE(retention_added_at, now()) "
            "WHERE job_id = CAST(:job_id AS uuid)",
            {"job_id": job_id},
        )
        if reward:
            _move(tx, user_id, job_id, "curve_reward", reward, 0)
            tx.execute(
                "UPDATE jobs SET curve_rewarded_at = now() WHERE job_id = CAST(:job_id AS uuid)",
                {"job_id": job_id},
            )
    return {"rewarded_cents": reward, "quality": list(quality_flags)}
