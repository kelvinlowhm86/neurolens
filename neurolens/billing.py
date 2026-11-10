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
CONSENT_VERSION = "2026-11-03"
# A session row older than this (by its last use) no longer signs anybody in (M3f §6b).
SESSION_DAYS = 30
# The reward preview's defaults when a caller does not supply config.json's research limits.
DEFAULT_REWARD_CAP = 5
DEFAULT_REWARD_WINDOW_DAYS = 30
DEFAULT_LENGTH_TOLERANCE_S = 2
DEFAULT_MIN_VIEWS = 1000
# A dispatched request that has not been claimed this long after its GPU wait is over is refunded.
GPU_WAIT_GRACE_S = 300
# STePS booth guest passes: 2 minutes ($4 / 400 cents) per visitor pass, 10 passes initially.
BOOTH_POOL_USER_ID = "booth-pool"
BOOTH_POOL_EMAIL = "booth-pool@booth.local"
BOOTH_PASS_CENTS = 400
BOOTH_INITIAL_PASSES = 10
GUEST_PREFIX = "guest-"

CURVE_REMOVED_MESSAGES = {
    "stopped": "You stopped sharing this video.",
    "no_curve": "This video's shared copy was deleted after 90 days without a curve.",
}


class BoothLimitReached(Exception):
    """All available booth passes in the pool have been claimed."""


class BoothPassEnded(Exception):
    """A guest account that was already deleted cannot be re-opened."""


class InsufficientCredit(Exception):
    def __init__(self, available_cents, required_cents):
        super().__init__(f"needs {required_cents} cents, {available_cents} available")
        self.available_cents = available_cents
        self.required_cents = required_cents


class JobActive(Exception):
    """A job is queued or processing, so deleting it (or its account) is refused."""


class AccountDeleted(Exception):
    """The account has been marked deleted, so it can no longer start work (M3c §5)."""


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


class VideoTaken(CurveRefused):
    """Another account already linked this published video (M3i §6)."""

    def __init__(self):
        super().__init__("video_link_taken", "This video is linked to another account.")


class VideoLinkChanged(CurveRefused):
    """This analysis is already linked to a different published video (M3i §6)."""

    def __init__(self):
        super().__init__("video_link_changed", "This analysis is already linked to another video.")


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


def _user_by_email(tx, email, *, lock=False):
    # `lock` holds the row until commit, so a sign-in and an account delete take turns (M3c §5).
    rows = tx.execute(
        "SELECT user_id FROM users WHERE lower(email) = lower(:email)"
        + (" FOR UPDATE" if lock else ""),
        {"email": email},
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
    abort the transaction (on Postgres and the Data API alike).

    The user row is locked as it is found, and a marked account is never found, so a sign-in
    racing `delete_account` either links before the mark (and the mark removes the link) or
    makes a new account after it (M3c §5)."""
    key = {"issuer": issuer, "subject": subject}
    find_identity = (
        "SELECT i.user_id FROM identities i JOIN users u ON u.user_id = i.user_id "
        "WHERE i.issuer = :issuer AND i.subject = :subject AND u.deleted_at IS NULL "
        "FOR UPDATE OF u"
    )
    with db.transaction() as tx:
        rows = tx.execute(find_identity, key)
        if rows:
            user_id = rows[0]["user_id"]
            _keep_email_current(tx, user_id, email)
            return user_id
        user_id = _user_by_email(tx, email, lock=True)
        if user_id is None:
            user_id = str(uuid.uuid4())
            if not _create_user(tx, user_id, email, starter_cents):
                user_id = _user_by_email(tx, email, lock=True)
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
    (M3b §7a). A waiting or declined access request is marked granted in the same transaction
    (M3k §4). Returns the user ID."""
    if not isinstance(cents, int) or isinstance(cents, bool) or cents <= 0:
        raise ValueError(f"cents must be a positive whole number, not {cents!r}")
    with db.transaction() as tx:
        user_id = _user_by_email(tx, email)
        if user_id is None:
            raise LookupError(f"no user with email {email!r}")
        _move(tx, user_id, None, "grant", cents, 0)
        tx.execute(
            "UPDATE access_requests SET status = :granted, decided_at = now() "
            "WHERE user_id = :user_id AND status <> :granted",
            {"user_id": user_id, "granted": "granted"},
        )
    return user_id


def _has_funding(tx, user_id):
    """True when the account has had credit beyond the starter (M3k §6)."""
    return bool(
        tx.execute(
            "SELECT 1 AS present FROM ledger WHERE user_id = :user_id "
            "AND (kind = :grant OR kind = :topup) LIMIT 1",
            {"user_id": user_id, "grant": "grant", "topup": "test_topup"},
        )
    )


def is_funded(db, user_id):
    """Whether the account has had credit beyond the starter (M3k §6), shared with /api/me."""
    with db.transaction() as tx:
        return _has_funding(tx, user_id)


def _reward_status(tx, user_id, reward_cap, reward_window_days):
    """(funded, under_cap) for the account, not the job (M3f §5d, M3j §5).

    `funded` means it has had credit beyond the starter; `under_cap` means fewer than
    `reward_cap` rewards in the window. Shared by `add_curve`, `curve_offer` and `list_jobs`, so
    the preview cannot drift from the payment and each refusal can name its own reason.
    """
    funded = _has_funding(tx, user_id)
    if not funded:
        return False, False
    recent = tx.execute(
        "SELECT count(*) AS n FROM ledger WHERE user_id = :user_id "
        "AND kind = :kind AND created_at > now() - "
        "make_interval(days => CAST(:days AS integer))",
        {"user_id": user_id, "kind": "curve_reward", "days": reward_window_days},
    )[0]["n"]
    return True, recent < reward_cap


def _curve_reward(
    job,
    *,
    funded,
    under_cap,
    same_video_rewarded=False,
    same_link_rewarded=False,
    valid=True,
    facts=None,
    length_tolerance_s=DEFAULT_LENGTH_TOLERANCE_S,
    min_views=DEFAULT_MIN_VIEWS,
    preview=False,
):
    """The reward a curve would pay and the first refusal (M3j §5, M3l §3).

    Conditions run in the spec's order: invalid (quality flags, save only), not_charged,
    already_rewarded (this job, the same video hash, or the same rewarded link on a job),
    not_funded, cap_reached, length_mismatch, too_few_views (M3l §3). `cents` is what the curve
    pays when `reason` is None, and what a check promises; the save pays only then.

    `preview` ignores the link and the length, which is what `list_jobs` shows before the form is
    filled in.
    """
    cents = job["captured_cents"] or 0

    def refuse(reason):
        return 0, reason

    if not valid:
        return refuse("invalid")
    if cents <= 0:
        return refuse("not_charged")
    if job["curve_rewarded_at"] is not None or same_video_rewarded or same_link_rewarded:
        return refuse("already_rewarded")
    if not funded:
        return refuse("not_funded")
    if not under_cap:
        return refuse("cap_reached")
    if preview:
        return cents, None
    if facts is not None:
        # The server's own measured length only; the page's figure is the user's word. A job
        # with no measured length cannot prove the match, so it is not paid.
        analysed_ms = job["verified_duration_ms"]
        if analysed_ms is None or abs(facts.duration_s - analysed_ms / 1000) > length_tolerance_s:
            return refuse("length_mismatch")
    if facts is not None and facts.views is not None and facts.views < min_views:
        return cents, "too_few_views"
    return cents, None


def list_jobs(
    db,
    user_id,
    limit,
    *,
    reward_cap=DEFAULT_REWARD_CAP,
    reward_window_days=DEFAULT_REWARD_WINDOW_DAYS,
):
    """The user's jobs, newest first, at most `limit` (M3b §4a, M3f §3, §5d).

    Deleted and cancelled jobs are left out. `claimed_at` is UTC text; `cancellable` marks a
    queued job the dispatcher has not priced yet (M3e §8). For a done, shared job, `sharing` is
    true while its shared copy is present, `curve_count` is its stored curves, and
    `curve_reward_cents` is what a valid curve would return now (0 when it cannot);
    `curve_rewarded` is true once a curve has returned this job's credit. The amount comes from
    the helper `add_curve` uses; the preview ignores the link and the length until the form
    supplies them.
    """
    with db.transaction() as tx:
        funded, under_cap = _reward_status(tx, user_id, reward_cap, reward_window_days)
        rows = tx.execute(
            "SELECT j.job_id, j.filename, j.status, j.created_at, j.verified_duration_ms, "
            "j.captured_cents, j.error_code, j.research_consent, j.claimed_at, "
            "j.gpu_wait_until, j.curve_rewarded_at, "
            "(j.status = :queued AND j.claimed_at IS NULL AND "
            "j.gpu_wait_until + make_interval(secs => :grace) < now()) AS gpu_wait_over, "
            "(j.status = :queued AND j.verified_duration_ms IS NULL) AS cancellable, "
            "(j.status = :done AND j.research_consent "
            "AND j.dataset_removed_at IS NULL) AS sharing, "
            "(SELECT count(*) FROM retention_snapshots s "
            "WHERE s.job_id = j.job_id) AS curve_count, "
            "(j.curve_rewarded_at IS NOT NULL) AS curve_rewarded, "
            "EXISTS (SELECT 1 FROM jobs other WHERE other.video_sha256 = j.video_sha256 "
            "AND other.video_sha256 IS NOT NULL AND other.curve_rewarded_at IS NOT NULL) "
            "AS same_video_rewarded "
            "FROM jobs j WHERE j.user_id = :user_id AND j.deleted_at IS NULL "
            "AND (j.error_code IS NULL OR j.error_code <> :cancelled) "
            "ORDER BY j.created_at DESC LIMIT :limit",
            {
                "user_id": user_id,
                "limit": limit,
                "done": DONE,
                "queued": QUEUED,
                "cancelled": "cancelled",
                "grace": GPU_WAIT_GRACE_S,
            },
        )
    for row in rows:
        if row["sharing"]:
            cents, _ = _curve_reward(
                row,
                funded=funded,
                under_cap=under_cap,
                same_video_rewarded=row["same_video_rewarded"],
                preview=True,
            )
        else:
            cents = 0
        row["curve_reward_cents"] = cents
        row.pop("same_video_rewarded")
        row.pop("curve_rewarded_at")
        if row["claimed_at"] is not None:
            row["claimed_at"] = row["claimed_at"].astimezone(UTC).strftime(storage.TIME_FORMAT)
    return rows


def delete_job(db, user_id, job_id):
    """Mark the user's job deleted under its row lock, keeping its row and ledger (M3c §4).

    This is the mark half of job deletion (M3f §5c): it happens before the files are purged, so
    no curve can be added while the purge runs. A copy purged earlier (Stop sharing, the 90-day
    rule) has its `dataset_purged_at` cleared, so its own files are purged now too. False,
    changing nothing, when the job is not the user's, already deleted, or still queued or
    processing.
    """
    with db.transaction() as tx:
        rows = tx.execute(
            "UPDATE jobs SET deleted_at = now(), filename = NULL, dataset_purged_at = NULL "
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

    The research prefix always goes, and the job's `retention_snapshots` rows go with it. A
    deleted job's own files (upload, result, brain, thumbnail) go too; a copy that was only
    removed keeps the user's own result. Deleting a job also removes every comparison it is part
    of, in S3 and in the database (M3h §6).
    """
    with db.transaction() as tx:
        rows = tx.execute(
            "SELECT object_key, deleted_at FROM jobs WHERE job_id = CAST(:job_id AS uuid)",
            {"job_id": job_id},
        )
        comparisons = (
            tx.execute(
                "SELECT job_a, job_b FROM comparisons WHERE "
                "job_a = CAST(:job_id AS uuid) OR job_b = CAST(:job_id AS uuid)",
                {"job_id": job_id},
            )
            if rows and rows[0]["deleted_at"] is not None
            else []
        )
    job = rows[0] if rows else None
    storage.delete_job_prefixes(s3, bucket, job_id)
    if job is not None and job["deleted_at"] is not None:
        keys = storage.job_keys(job_id, job["object_key"])
        keys += [storage.comparison_key(row["job_a"], row["job_b"]) for row in comparisons]
        if not storage.delete_keys(s3, bucket, keys):
            raise RuntimeError(f"{job_id}: S3 reported a failed delete")
    with db.transaction() as tx:
        tx.execute(
            "DELETE FROM retention_snapshots WHERE job_id = CAST(:job_id AS uuid)",
            {"job_id": job_id},
        )
        if job is not None and job["deleted_at"] is not None:
            tx.execute(
                "DELETE FROM comparisons WHERE "
                "job_a = CAST(:job_id AS uuid) OR job_b = CAST(:job_id AS uuid)",
                {"job_id": job_id},
            )
    mark_dataset_purged(db, job_id, own_files=job is not None and job["deleted_at"] is not None)


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


def mark_dataset_purged(db, job_id, *, own_files=False):
    """Record that a marked copy's files are gone (M3f §5c). False if it was not marked.

    A purge that did not delete the job's own files (`own_files` false) records nothing once
    the job has been deleted meanwhile, so that purge is still owed.
    """
    with db.transaction() as tx:
        rows = tx.execute(
            "UPDATE jobs SET dataset_purged_at = now() "
            "WHERE job_id = CAST(:job_id AS uuid) AND dataset_purged_at IS NULL "
            "AND (dataset_removed_at IS NOT NULL OR deleted_at IS NOT NULL) "
            "AND (deleted_at IS NULL OR :own_files) RETURNING job_id",
            {"job_id": job_id, "own_files": own_files},
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
    """Mark a user's account and jobs deleted and return its `(issuer, subject)` identities
    (M3c §5). Refuses with JobActive while any of their jobs is queued or processing.

    Lock order: every caller takes the balance row before job rows. Here the balance row is
    locked first, so a racing `reserve` either wins before the mark or is refused after it. The
    email is replaced at once, and the sessions and identities are gone, so every tab is signed
    out and signing in again makes a new, empty account. The access request is deleted too: it
    holds only a channel link and a note (M3k §4). `remove_deleted_account` deletes the rows
    once every job's files are purged (M3f §5c).
    """
    with db.transaction() as tx:
        locked = tx.execute(
            "SELECT user_id FROM balances WHERE user_id = :user_id FOR UPDATE",
            {"user_id": user_id},
        )
        if not locked:
            raise LookupError(f"no balance for user {user_id!r}: call ensure_user first")
        # The user row next: a racing sign-in waits for the mark, then no longer finds it.
        tx.execute(
            "SELECT user_id FROM users WHERE user_id = :user_id FOR UPDATE", {"user_id": user_id}
        )
        active = tx.execute(
            "SELECT 1 AS active FROM jobs WHERE user_id = :user_id AND status IN "
            "(:queued, :processing) LIMIT 1",
            {"user_id": user_id, "queued": QUEUED, "processing": PROCESSING},
        )
        if active:
            raise JobActive(f"user {user_id!r} has an active job")
        tx.execute(
            "UPDATE jobs SET deleted_at = now(), filename = NULL, dataset_purged_at = NULL "
            "WHERE user_id = :user_id",
            {"user_id": user_id},
        )
        tx.execute("DELETE FROM access_requests WHERE user_id = :user_id", {"user_id": user_id})
        tx.execute("DELETE FROM sessions WHERE user_id = :user_id", {"user_id": user_id})
        identities = tx.execute(
            "DELETE FROM identities WHERE user_id = :user_id RETURNING issuer, subject",
            {"user_id": user_id},
        )
        tx.execute(
            "UPDATE users SET deleted_at = now(), email = :email WHERE user_id = :user_id",
            {"user_id": user_id, "email": f"deleted:{user_id}"},
        )
    return [(row["issuer"], row["subject"]) for row in identities]


def remove_deleted_account(db, user_id):
    """Delete a marked account's rows once every job's files are gone (M3c §5, M3f §5c).

    Only when the user has `deleted_at` and every job has `dataset_purged_at`; otherwise change
    nothing and return False. Credit is free, so no financial record is kept. The access request
    goes too, before the user row: it may have raced the delete (M3k §4). Locks the balance row
    first, like every money-or-job transaction.
    """
    with db.transaction() as tx:
        tx.execute(
            "SELECT user_id FROM balances WHERE user_id = :user_id FOR UPDATE",
            {"user_id": user_id},
        )
        current = tx.execute(
            "SELECT deleted_at FROM users WHERE user_id = :user_id", {"user_id": user_id}
        )
        if not current or current[0]["deleted_at"] is None:
            return False
        pending = tx.execute(
            "SELECT 1 AS pending FROM jobs WHERE user_id = :user_id "
            "AND dataset_purged_at IS NULL LIMIT 1",
            {"user_id": user_id},
        )
        if pending:
            return False
        tx.execute("DELETE FROM access_requests WHERE user_id = :user_id", {"user_id": user_id})
        tx.execute("DELETE FROM ledger WHERE user_id = :user_id", {"user_id": user_id})
        tx.execute("DELETE FROM jobs WHERE user_id = :user_id", {"user_id": user_id})
        tx.execute("DELETE FROM balances WHERE user_id = :user_id", {"user_id": user_id})
        tx.execute("DELETE FROM users WHERE user_id = :user_id", {"user_id": user_id})
    return True


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
    row lock makes the count and the new job one decision per user. Raises AccountDeleted
    (changing nothing) once the account's delete has marked it (M3c §5).
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
        deleted = tx.execute(
            "SELECT 1 AS deleted FROM users WHERE user_id = :user_id "
            "AND deleted_at IS NOT NULL LIMIT 1",
            {"user_id": user_id},
        )
        if deleted:
            raise AccountDeleted(f"user {user_id!r} has been deleted")
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


# ---------------------------------------------------------------- the GPU wait


def record_gpu_wait(db, job_id, until):
    """Record when a dispatched request's GPU wait runs out, while the job is still queued
    (M3l §2b). False, changing nothing, once a worker has claimed it."""
    seconds = max(0, round((until - datetime.now(UTC)).total_seconds()))
    with db.transaction() as tx:
        rows = tx.execute(
            "UPDATE jobs SET gpu_wait_until = now() + make_interval(secs => :seconds) "
            "WHERE job_id = CAST(:job_id AS uuid) AND status = :queued RETURNING job_id",
            {"job_id": job_id, "seconds": seconds, "queued": QUEUED},
        )
    return bool(rows)


def refund_if_gpu_wait_over(db, job_id):
    """Refund a queued, never-claimed job whose recorded GPU wait ended more than the grace ago
    (M3l §2c). True when this call refunded it; False, changing nothing, otherwise.

    The row lock makes this and a worker's claim mutually exclusive, so exactly one wins.
    """
    with db.transaction() as tx:
        _lock_job_balance(tx, job_id)
        rows = tx.execute(
            "SELECT user_id, status, attempt, reserved_cents, claimed_at, "
            "gpu_wait_until IS NOT NULL AND "
            "gpu_wait_until + make_interval(secs => :grace) < now() AS expired "
            "FROM jobs WHERE job_id = CAST(:job_id AS uuid) FOR UPDATE",
            {"job_id": job_id, "grace": GPU_WAIT_GRACE_S},
        )
        job = rows[0] if rows else None
        if (
            job is None
            or job["status"] != QUEUED
            or job["claimed_at"] is not None
            or not job["expired"]
        ):
            return False
        _refund(tx, job_id, job, "no_gpu_free", None)
    return True


# ---------------------------------------------------------------- the AI report


def claim_report(db, user_id, job_id, max_tries, stale_minutes):
    """Claim one report attempt for the user's done job (M3g §3). True if this call got it.

    It may claim a report never started, one that failed, or a `writing` one older than
    `stale_minutes` (a Lambda that died); never a fresh `writing` one or one at its try cap.
    """
    with db.transaction() as tx:
        rows = tx.execute(
            "UPDATE jobs SET report_state = :writing, report_tries = report_tries + 1, "
            "report_started_at = now(), report_error = NULL "
            "WHERE job_id = CAST(:job_id AS uuid) AND user_id = :user_id "
            "AND status = :done AND deleted_at IS NULL AND report_tries < :max_tries "
            "AND EXISTS (SELECT 1 FROM users u WHERE u.user_id = :user_id "
            "AND u.deleted_at IS NULL) "
            "AND (report_state IS NULL OR report_state = :failed OR "
            "(report_state = :writing AND report_started_at < now() - "
            "make_interval(mins => CAST(:stale AS integer)))) RETURNING job_id",
            {
                "job_id": job_id,
                "user_id": user_id,
                "done": DONE,
                "writing": "writing",
                "failed": "failed",
                "max_tries": max_tries,
                "stale": stale_minutes,
            },
        )
    return bool(rows)


def finish_report(db, job_id, state, error=None):
    """Finish a `writing` report as `ready` or `failed` (M3g §3). False if no row changed."""
    with db.transaction() as tx:
        rows = tx.execute(
            "UPDATE jobs SET report_state = :state, report_error = :error "
            "WHERE job_id = CAST(:job_id AS uuid) AND report_state = :writing "
            "AND deleted_at IS NULL RETURNING job_id",
            {
                "job_id": job_id,
                "state": state,
                "error": error,
                "writing": "writing",
            },
        )
    return bool(rows)


def release_report_try(db, job_id):
    """Mark a report failed and give its try back, for a Lambda that could not start (M3g §4)."""
    with db.transaction() as tx:
        tx.execute(
            "UPDATE jobs SET report_state = :failed, report_tries = report_tries - 1 "
            "WHERE job_id = CAST(:job_id AS uuid) AND report_state = :writing",
            {"job_id": job_id, "failed": "failed", "writing": "writing"},
        )


# ---------------------------------------------------------------- the comparison report


def claim_comparison(db, user_id, job_a, job_b, max_tries, stale_minutes):
    """Claim one comparison attempt for two of the user's done jobs (M3h §3).

    Both job rows are locked first, so a racing delete either finishes before the mark (the purge
    sees this row) or marks the job first and the claim sees it. The canonical pair means B-vs-A
    can never start a second report. True when this call got the claim.
    """
    jobs = "job_id IN (CAST(:job_a AS uuid), CAST(:job_b AS uuid))"
    eligible = (
        f"SELECT count(*) FROM jobs WHERE {jobs} "
        "AND user_id = :user_id AND status = :done AND deleted_at IS NULL"
    )
    with db.transaction() as tx:
        tx.execute(
            f"SELECT job_id FROM jobs WHERE {jobs} ORDER BY job_id FOR SHARE",
            {"job_a": job_a, "job_b": job_b},
        )
        rows = tx.execute(
            "INSERT INTO comparisons (job_a, job_b, state, tries, started_at) "
            "SELECT CAST(:job_a AS uuid), CAST(:job_b AS uuid), :writing, 1, now() "
            f"WHERE ({eligible}) = 2 "
            "AND EXISTS (SELECT 1 FROM users WHERE user_id = :user_id AND deleted_at IS NULL) "
            "ON CONFLICT (job_a, job_b) DO UPDATE "
            "SET state = :writing, tries = comparisons.tries + 1, started_at = now(), "
            "error = NULL "
            "WHERE comparisons.tries < :max_tries "
            "AND (comparisons.state = :failed OR (comparisons.state = :writing "
            "AND comparisons.started_at < now() - "
            "make_interval(mins => CAST(:stale AS integer)))) "
            f"AND ({eligible}) = 2 "
            "AND EXISTS (SELECT 1 FROM users WHERE user_id = :user_id AND deleted_at IS NULL) "
            "RETURNING job_a",
            {
                "job_a": job_a,
                "job_b": job_b,
                "user_id": user_id,
                "done": DONE,
                "writing": "writing",
                "failed": "failed",
                "max_tries": max_tries,
                "stale": stale_minutes,
            },
        )
    return bool(rows)


def finish_comparison(db, job_a, job_b, state, error=None):
    """Finish a `writing` comparison if neither job is deleted (M3h §3). False if no row changed."""
    with db.transaction() as tx:
        rows = tx.execute(
            "UPDATE comparisons SET state = :state, error = :error "
            "WHERE job_a = CAST(:job_a AS uuid) AND job_b = CAST(:job_b AS uuid) "
            "AND state = :writing "
            "AND NOT EXISTS (SELECT 1 FROM jobs WHERE "
            "job_id IN (CAST(:job_a AS uuid), CAST(:job_b AS uuid)) "
            "AND deleted_at IS NOT NULL) RETURNING job_a",
            {
                "job_a": job_a,
                "job_b": job_b,
                "state": state,
                "error": error,
                "writing": "writing",
            },
        )
    return bool(rows)


def release_comparison_try(db, job_a, job_b):
    """Mark a comparison failed and give its try back, for a Lambda that could not start
    (M3h §4)."""
    with db.transaction() as tx:
        tx.execute(
            "UPDATE comparisons SET state = :failed, tries = tries - 1 "
            "WHERE job_a = CAST(:job_a AS uuid) AND job_b = CAST(:job_b AS uuid) "
            "AND state = :writing",
            {"job_a": job_a, "job_b": job_b, "failed": "failed", "writing": "writing"},
        )


def get_comparison(db, job_a, job_b):
    """The comparison row (`state`, `tries`, `started_at`) or None (M3h §3)."""
    with db.transaction() as tx:
        rows = tx.execute(
            "SELECT state, tries, started_at FROM comparisons "
            "WHERE job_a = CAST(:job_a AS uuid) AND job_b = CAST(:job_b AS uuid)",
            {"job_a": job_a, "job_b": job_b},
        )
    return rows[0] if rows else None


# ---------------------------------------------------------------- retention curves


def _text_array(values):
    """A PostgreSQL text[] literal for a parameter, as neither backend can bind a Python list."""

    def quoted(value):
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    return "{" + ",".join(quoted(value) for value in values) + "}"


def _curve_state(tx, user_id, job_id, video_ref, *, lock=False):
    """The job checks and link refusals shared by `add_curve` and `curve_offer` (M3j §5).

    Returns `(job, same_video_rewarded, same_link_rewarded)`. The job must be the caller's, done,
    shared, not removed and not deleted. A link another account already used raises VideoTaken,
    and a job already linked to a different video raises VideoLinkChanged (M3i §6). The
    once-per-link reward guard reads `jobs.curve_reward_ref`, never `retention_snapshots`, so a
    purged copy cannot forget a reward. `lock` takes the job row lock, as the save must; the
    check is read-only and leaves it alone.
    """
    rows = tx.execute(
        "SELECT user_id, status, research_consent, dataset_removed_at, "
        "dataset_removed_reason, deleted_at, captured_cents, curve_rewarded_at, video_sha256, "
        "verified_duration_ms "
        "FROM jobs WHERE job_id = CAST(:job_id AS uuid)" + (" FOR UPDATE" if lock else ""),
        {"job_id": job_id},
    )
    job = rows[0] if rows else None
    if job is None or job["user_id"] != user_id or job["deleted_at"] is not None:
        raise CurveRefused("not_found", "No such job.", status=404)
    if job["status"] != DONE:
        raise CurveRefused("job_not_done", "The job is not done yet.")
    if not job["research_consent"]:
        raise CurveRefused("no_consent", "This video was not shared.")
    if job["dataset_removed_at"] is not None:
        message = CURVE_REMOVED_MESSAGES.get(
            job["dataset_removed_reason"], CURVE_REMOVED_MESSAGES["no_curve"]
        )
        raise CurveRefused("dataset_removed", message)

    taken = tx.execute(
        "SELECT 1 AS present FROM retention_snapshots s "
        "JOIN jobs j ON j.job_id = s.job_id "
        "WHERE s.video_ref = :video_ref AND j.user_id <> :user_id LIMIT 1",
        {"video_ref": video_ref, "user_id": user_id},
    )
    if taken:
        raise VideoTaken()
    changed = tx.execute(
        "SELECT 1 AS present FROM retention_snapshots WHERE job_id = CAST(:job_id AS uuid) "
        "AND video_ref IS NOT NULL AND video_ref <> :video_ref LIMIT 1",
        {"job_id": job_id, "video_ref": video_ref},
    )
    if changed:
        raise VideoLinkChanged()

    same_video = tx.execute(
        "SELECT 1 AS present FROM jobs other WHERE other.video_sha256 = :sha "
        "AND other.video_sha256 IS NOT NULL AND other.curve_rewarded_at IS NOT NULL LIMIT 1",
        {"sha": job["video_sha256"]},
    )
    same_link = tx.execute(
        "SELECT 1 AS present FROM jobs WHERE curve_reward_ref = :video_ref LIMIT 1",
        {"video_ref": video_ref},
    )
    return job, bool(same_video), bool(same_link)


def curve_offer(
    db,
    user_id,
    job_id,
    video_ref,
    facts,
    reward_cap,
    reward_window_days,
    length_tolerance_s,
    min_views=DEFAULT_MIN_VIEWS,
):
    """What `add_curve` would pay for this link, without writing anything (M3j §5-§6).

    The same job checks, link refusals and reward helper as the save, so the check's answer and
    the save's can never disagree. Returns `(cents, reason)`.
    """
    with db.transaction() as tx:
        job, same_video, same_link = _curve_state(tx, user_id, job_id, video_ref)
        funded, under_cap = _reward_status(tx, user_id, reward_cap, reward_window_days)
        return _curve_reward(
            job,
            funded=funded,
            under_cap=under_cap,
            same_video_rewarded=same_video,
            same_link_rewarded=same_link,
            facts=facts,
            length_tolerance_s=length_tolerance_s,
            min_views=min_views,
        )


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
    video_ref,
    same_cut,
    source,
    facts=None,
    length_tolerance_s=DEFAULT_LENGTH_TOLERANCE_S,
    min_views=DEFAULT_MIN_VIEWS,
):
    """Store one retention snapshot and reward the analysis when all the rules allow it.

    One transaction with the balance row locked before the job row (M3f §5c, §5d): the job checks
    and link refusals are `_curve_state`'s, shared with `curve_offer` (M3j §5). A curve already
    stored under the same hash, on any job or account, raises DuplicateCurve and stores nothing.
    A valid curve of a charged job returns the captured price once, when the reward rules allow
    it: with the video's length within `length_tolerance_s` when it can be checked and at least
    `min_views` checked views when the count is known (M3l §3), when neither the video hash
    nor the published link (`jobs.curve_reward_ref`) has been rewarded, by an account that has
    had credit beyond the starter and below the per-window cap. The checked facts (views, publish
    date, length) are stored too. All checks run before anything is written, so a refusal stores
    nothing.
    """
    with db.transaction() as tx:
        _lock_balance(tx, user_id)
        job, same_video, same_link = _curve_state(tx, user_id, job_id, video_ref, lock=True)
        duplicate = tx.execute(
            "SELECT 1 AS present FROM retention_snapshots WHERE curve_sha256 = :sha LIMIT 1",
            {"sha": curve_sha256},
        )
        if duplicate:
            raise DuplicateCurve("this curve was already added")

        funded, under_cap = _reward_status(tx, user_id, reward_cap, reward_window_days)
        cents, reason = _curve_reward(
            job,
            funded=funded,
            under_cap=under_cap,
            same_video_rewarded=same_video,
            same_link_rewarded=same_link,
            valid=not quality_flags,
            facts=facts,
            length_tolerance_s=length_tolerance_s,
            min_views=min_views,
        )
        reward = cents if reason is None else 0

        tx.execute(
            "INSERT INTO retention_snapshots (snapshot_id, job_id, raw_key, csv_key, "
            "curve_sha256, points, quality_flags, views, published, promoted, rewarded_cents, "
            "video_ref, same_cut, source, checked_views, published_at, video_seconds) "
            "VALUES (CAST(:snapshot_id AS uuid), CAST(:job_id AS uuid), :raw_key, :csv_key, "
            ":sha, :points, CAST(:flags AS text[]), :views, :published, :promoted, :reward, "
            ":video_ref, :same_cut, :source, :checked_views, :published_at, :video_seconds)",
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
                "video_ref": video_ref,
                "same_cut": same_cut,
                "source": source,
                "checked_views": facts.views if facts is not None else None,
                "published_at": facts.published_at if facts is not None else None,
                "video_seconds": facts.duration_s if facts is not None else None,
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
                "UPDATE jobs SET curve_rewarded_at = now(), curve_reward_ref = :video_ref "
                "WHERE job_id = CAST(:job_id AS uuid)",
                {"job_id": job_id, "video_ref": video_ref},
            )
    return {"rewarded_cents": reward, "quality": list(quality_flags), "reason": reason}


# ---------------------------------------------------------------- STePS booth guest passes


def is_booth_guest(user_id):
    """True when `user_id` is an ephemeral booth guest account."""
    return isinstance(user_id, str) and user_id.startswith(GUEST_PREFIX)


def _ensure_booth_pool(tx):
    """Create the booth pass pool once with the initial 10 passes."""
    _create_user(tx, BOOTH_POOL_USER_ID, BOOTH_POOL_EMAIL, BOOTH_INITIAL_PASSES * BOOTH_PASS_CENTS)


def _read_booth_gpu_warm(tx):
    """Return the active booth GPU keep-warm target (0, 1, or 2), expiring 6 hours after click."""
    rows = tx.execute(
        "SELECT session_hash, "
        "(last_seen_at < now() - make_interval(hours => 6)) AS expired "
        "FROM sessions WHERE user_id = :user_id LIMIT 1",
        {"user_id": BOOTH_POOL_USER_ID},
    )
    if not rows:
        return 0
    sh = rows[0]["session_hash"]
    target = 1 if sh == "booth-gpu-warm-1" else (2 if sh == "booth-gpu-warm-2" else 0)
    if target == 0 or rows[0]["expired"]:
        tx.execute(
            "DELETE FROM sessions WHERE user_id = :user_id",
            {"user_id": BOOTH_POOL_USER_ID},
        )
        return 0
    return target


def set_booth_gpu_warm(db, target):
    """Set the booth GPU keep-warm target (0 for auto-scale, or 1 / 2 to hold warm)."""
    if type(target) is not int or target not in (0, 1, 2):
        raise ValueError(f"target must be 0, 1 or 2, not {target!r}")
    with db.transaction() as tx:
        _ensure_booth_pool(tx)
        tx.execute(
            "DELETE FROM sessions WHERE user_id = :user_id",
            {"user_id": BOOTH_POOL_USER_ID},
        )
        if target in (1, 2):
            tx.execute(
                "INSERT INTO sessions (session_hash, user_id, last_seen_at) "
                "VALUES (:session_hash, :user_id, now())",
                {
                    "session_hash": f"booth-gpu-warm-{target}",
                    "user_id": BOOTH_POOL_USER_ID,
                },
            )
        return _read_booth_stats(tx)


def _read_booth_stats(tx, *, check_pass_id=None):
    pool = tx.execute(
        "SELECT available_cents FROM balances WHERE user_id = :user_id",
        {"user_id": BOOTH_POOL_USER_ID},
    )
    remaining = (pool[0]["available_cents"] if pool else 0) // BOOTH_PASS_CENTS
    prefix = f"{GUEST_PREFIX}%"
    pass_days = tx.execute(
        "SELECT CAST((created_at AT TIME ZONE :tz) AS date) AS day, count(*) AS n "
        "FROM users WHERE user_id LIKE :prefix GROUP BY day",
        {"prefix": prefix, "tz": "Asia/Singapore"},
    )
    job_days = tx.execute(
        "SELECT CAST((created_at AT TIME ZONE :tz) AS date) AS day, "
        "count(*) FILTER (WHERE status = :done) AS videos_done, "
        "count(*) FILTER (WHERE status IN (:queued, :processing)) AS videos_running, "
        "count(*) FILTER (WHERE report_state = :ready) AS reports_ready "
        "FROM jobs WHERE user_id LIKE :prefix AND deleted_at IS NULL GROUP BY day",
        {
            "prefix": prefix,
            "tz": "Asia/Singapore",
            "done": DONE,
            "queued": QUEUED,
            "processing": PROCESSING,
            "ready": "ready",
        },
    )
    comp_days = tx.execute(
        "SELECT CAST((c.started_at AT TIME ZONE :tz) AS date) AS day, count(*) AS n "
        "FROM comparisons c JOIN jobs j ON j.job_id = c.job_a "
        "WHERE j.user_id LIKE :prefix AND c.state = :ready GROUP BY day",
        {"prefix": prefix, "tz": "Asia/Singapore", "ready": "ready"},
    )
    claimed = sum(row["n"] for row in pass_days)
    videos_done = sum(row["videos_done"] for row in job_days)
    videos_running = sum(row["videos_running"] for row in job_days)
    reports_ready = sum(row["reports_ready"] for row in job_days)
    comparisons_ready = sum(row["n"] for row in comp_days)
    by_day = {}
    for row in pass_days:
        day = str(row["day"])
        by_day.setdefault(
            day,
            {
                "date": day,
                "passes": 0,
                "videos_done": 0,
                "reports_ready": 0,
                "comparisons_ready": 0,
            },
        )["passes"] = row["n"]
    for row in job_days:
        day = str(row["day"])
        entry = by_day.setdefault(
            day,
            {
                "date": day,
                "passes": 0,
                "videos_done": 0,
                "reports_ready": 0,
                "comparisons_ready": 0,
            },
        )
        entry["videos_done"] = row["videos_done"]
        entry["reports_ready"] = row["reports_ready"]
    for row in comp_days:
        day = str(row["day"])
        by_day.setdefault(
            day,
            {
                "date": day,
                "passes": 0,
                "videos_done": 0,
                "reports_ready": 0,
                "comparisons_ready": 0,
            },
        )["comparisons_ready"] = row["n"]
    days = [by_day[k] for k in sorted(by_day, reverse=True)]
    pass_claimed = False
    if isinstance(check_pass_id, str) and check_pass_id:
        guest_id = f"{GUEST_PREFIX}{check_pass_id}"
        pass_claimed = bool(
            tx.execute(
                "SELECT 1 AS present FROM users WHERE user_id = :user_id LIMIT 1",
                {"user_id": guest_id},
            )
        )
    return {
        "passes_claimed": claimed,
        "passes_remaining": remaining,
        "videos_done": videos_done,
        "videos_running": videos_running,
        "reports_ready": reports_ready,
        "comparisons_ready": comparisons_ready,
        "days": days,
        "gpu_warm_target": _read_booth_gpu_warm(tx),
        "pass_claimed": pass_claimed,
    }


def booth_stats(db, *, check_pass_id=None):
    """Anonymous booth pass and job totals across `guest-*` accounts."""
    with db.transaction() as tx:
        _ensure_booth_pool(tx)
        return _read_booth_stats(tx, check_pass_id=check_pass_id)


def adjust_booth_passes(db, delta_passes):
    """Add `delta_passes` (> 0) to the booth pool, or clear remaining passes when 0."""
    if not isinstance(delta_passes, int) or isinstance(delta_passes, bool) or delta_passes < 0:
        raise ValueError(f"delta_passes must be a non-negative integer, not {delta_passes!r}")
    with db.transaction() as tx:
        _ensure_booth_pool(tx)
        pool = _lock_balance(tx, BOOTH_POOL_USER_ID)
        avail = pool["available_cents"] if pool else 0
        if delta_passes == 0:
            if avail > 0:
                _move(tx, BOOTH_POOL_USER_ID, None, "grant", -avail, 0)
        else:
            _move(tx, BOOTH_POOL_USER_ID, None, "grant", delta_passes * BOOTH_PASS_CENTS, 0)
        return _read_booth_stats(tx)


def redeem_booth_pass(db, pass_id, *, current_user_id=None):
    """Redeem one booth QR pass into an isolated `guest-<pass_id>` user with 2 min of credit.

    Idempotent: re-scanning the same QR code returns the existing guest user without consuming a
    second pass from the pool. If the caller already has an active `guest-*` session on their
    device (`current_user_id`), returns that existing guest account so closing the tab and
    scanning a new QR code brings them back to their videos without consuming another pass.
    Raises BoothPassEnded if that guest account was deleted, or BoothLimitReached if the pool has
    no remaining passes.
    """
    guest_id = f"{GUEST_PREFIX}{pass_id}"
    with db.transaction() as tx:
        _ensure_booth_pool(tx)
        if is_booth_guest(current_user_id) and current_user_id != guest_id:
            active_current = tx.execute(
                "SELECT deleted_at FROM users WHERE user_id = :user_id",
                {"user_id": current_user_id},
            )
            if active_current and active_current[0]["deleted_at"] is None:
                return current_user_id
        pool = _lock_balance(tx, BOOTH_POOL_USER_ID)
        existing = tx.execute(
            "SELECT deleted_at FROM users WHERE user_id = :user_id FOR UPDATE",
            {"user_id": guest_id},
        )
        if existing:
            if existing[0]["deleted_at"] is not None:
                raise BoothPassEnded(f"booth pass {pass_id!r} was deleted")
            return guest_id
        if not pool or pool["available_cents"] < BOOTH_PASS_CENTS:
            raise BoothLimitReached("no booth passes remaining")
        _move(tx, BOOTH_POOL_USER_ID, None, "grant", -BOOTH_PASS_CENTS, 0)
        _create_user(tx, guest_id, f"{guest_id}@booth.local", BOOTH_PASS_CENTS)
    return guest_id
