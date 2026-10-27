"""
Job lifecycle. Every status change is one SQL statement guarded by the status it expects
(and, for worker writes, by worker_id), so concurrent actors can never read-modify-write past
each other.

    AWAITING_UPLOAD ──(browser says uploaded)──▶ UPLOADED
    AWAITING_UPLOAD / UPLOADED / stale PROCESSING ──(worker claim)──▶ PROCESSING
    PROCESSING ──▶ SUCCEEDED (capture)  |  FAILED (release)  |  UPLOADED (Spot requeue)
    AWAITING_UPLOAD ──▶ EXPIRED / CANCELLED (release)
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from psycopg.types.json import Jsonb

from . import ledger

STALE_HEARTBEAT_SECONDS = 90


class JobStatus(StrEnum):
    AWAITING_UPLOAD = "AWAITING_UPLOAD"
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


TERMINAL = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.EXPIRED, JobStatus.CANCELLED}

# Worker stages → the Experiment 1 timestamp column stamped when the stage finishes.
STAGE_DONE_COLUMN = {
    "DOWNLOADING": "downloaded_at",
    "VALIDATING": "validated_at",
    "PASS_FULL": "pass_full_done_at",
    "PASS_SILENT": "pass_silent_done_at",
    "ROI": "roi_done_at",
}


# ─── Web tier ──────────────────────────────────────────────────────────────


def create_job(conn, *, job_id: UUID, user_id: UUID, filename: str, content_type: str,
               size_bytes: int, s3_input_key: str, est_duration_s: float) -> None:
    conn.execute(
        """INSERT INTO jobs (id, user_id, original_filename, content_type, size_bytes,
                             s3_input_key, est_duration_s)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (job_id, user_id, filename, content_type, size_bytes, s3_input_key, est_duration_s),
    )


def count_active_jobs(conn, user_id: UUID) -> int:
    return conn.execute(
        """SELECT count(*) AS n FROM jobs
            WHERE user_id = %s AND status IN ('AWAITING_UPLOAD', 'UPLOADED', 'PROCESSING')""",
        (user_id,),
    ).fetchone()["n"]


def count_recent_creates(conn, user_id: UUID, seconds: int = 60) -> int:
    return conn.execute(
        "SELECT count(*) AS n FROM jobs WHERE user_id = %s AND created_at > now() - make_interval(secs => %s)",
        (user_id, seconds),
    ).fetchone()["n"]


def count_demand(conn) -> int:
    """Jobs that need (or will soon need) a GPU — drives the pre-warm nudge."""
    return conn.execute(
        """SELECT count(*) AS n FROM jobs
            WHERE status IN ('UPLOADED', 'PROCESSING')
               OR (status = 'AWAITING_UPLOAD' AND created_at > now() - interval '15 minutes')"""
    ).fetchone()["n"]


def set_prewarm_action(conn, job_id: UUID, action: str) -> None:
    conn.execute("UPDATE jobs SET prewarm_action = %s WHERE id = %s", (action, job_id))


def mark_uploaded(conn, job_id: UUID, user_id: UUID) -> bool:
    """Browser reports the upload finished. Advisory only: the S3 event is what triggers work."""
    return conn.execute(
        """UPDATE jobs SET status = 'UPLOADED', upload_confirmed_at = now()
            WHERE id = %s AND user_id = %s AND status = 'AWAITING_UPLOAD'""",
        (job_id, user_id),
    ).rowcount == 1


def record_client_render(conn, job_id: UUID, user_id: UUID, ms: int) -> None:
    conn.execute(
        "UPDATE jobs SET client_render_ms = %s WHERE id = %s AND user_id = %s AND status = 'SUCCEEDED'",
        (ms, job_id, user_id),
    )


def get_job(conn, job_id: UUID, user_id: UUID | None = None) -> dict | None:
    if user_id is None:
        return conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()
    return conn.execute("SELECT * FROM jobs WHERE id = %s AND user_id = %s", (job_id, user_id)).fetchone()


def list_jobs(conn, user_id: UUID, limit: int = 20, before: datetime | None = None) -> list[dict]:
    return conn.execute(
        """SELECT id, status, original_filename, actual_duration_s, est_duration_s, credits_captured,
                  error_code, summary, created_at, completed_at
             FROM jobs
            WHERE user_id = %s AND (%s::timestamptz IS NULL OR created_at < %s)
            ORDER BY created_at DESC LIMIT %s""",
        (user_id, before, before, limit),
    ).fetchall()


def queue_position(conn, job: dict) -> int | None:
    """How many uploaded jobs are ahead of this one (None once it is being processed)."""
    if job["status"] not in (JobStatus.AWAITING_UPLOAD, JobStatus.UPLOADED):
        return None
    return conn.execute(
        "SELECT count(*) AS n FROM jobs WHERE status = 'UPLOADED' AND created_at < %s",
        (job["created_at"],),
    ).fetchone()["n"]


# ─── Worker ────────────────────────────────────────────────────────────────


def claim_job(conn, job_id: UUID, worker_id: str, s3_event_at: datetime | None) -> dict | None:
    """
    Atomically take a job. Exactly one of any number of concurrent callers gets a row back.

    A job already PROCESSING is claimable only if its heartbeat is stale (its worker died),
    which is how SQS redelivery after a crash resumes work.
    """
    return conn.execute(
        f"""UPDATE jobs
               SET status = 'PROCESSING', worker_id = %(w)s, attempts = attempts + 1,
                   claimed_at = now(), heartbeat_at = now(),
                   progress_stage = 'DOWNLOADING', progress_pct = 0,
                   s3_event_at = COALESCE(s3_event_at, %(evt)s)
             WHERE id = %(job)s
               AND (status IN ('AWAITING_UPLOAD', 'UPLOADED')
                    OR (status = 'PROCESSING'
                        AND heartbeat_at < now() - interval '{STALE_HEARTBEAT_SECONDS} seconds'))
         RETURNING id, user_id, s3_input_key, original_filename, credits_reserved, attempts""",
        {"job": job_id, "w": worker_id, "evt": s3_event_at},
    ).fetchone()


def heartbeat(conn, job_id: UUID, worker_id: str) -> bool:
    """False means this worker no longer owns the job and must stop."""
    return conn.execute(
        """UPDATE jobs SET heartbeat_at = now()
            WHERE id = %s AND worker_id = %s AND status = 'PROCESSING'""",
        (job_id, worker_id),
    ).rowcount == 1


def set_stage(conn, job_id: UUID, worker_id: str, stage: str, pct: int,
              finished_stage: str | None = None) -> bool:
    """Move to `stage`; if `finished_stage` is given, stamp its Experiment 1 timestamp."""
    stamp = ""
    if finished_stage:
        stamp = f", {STAGE_DONE_COLUMN[finished_stage]} = now()"  # whitelisted column name
    return conn.execute(
        f"""UPDATE jobs SET progress_stage = %s, progress_pct = %s, heartbeat_at = now(){stamp}
             WHERE id = %s AND worker_id = %s AND status = 'PROCESSING'""",
        (stage, pct, job_id, worker_id),
    ).rowcount == 1


def set_actual_duration(conn, job_id: UUID, worker_id: str, seconds: float) -> None:
    conn.execute(
        "UPDATE jobs SET actual_duration_s = %s WHERE id = %s AND worker_id = %s",
        (seconds, job_id, worker_id),
    )


def complete_job(conn, job_id: UUID, worker_id: str, *, credits: int, result_key: str,
                 summary: dict, gpu_device: str | None, peak_vram_gb: float | None,
                 cold_start: bool) -> bool:
    """Mark SUCCEEDED and charge, in one transaction. False if this worker lost the job."""
    with conn.transaction():
        row = conn.execute(
            """UPDATE jobs
                  SET status = 'SUCCEEDED', s3_result_key = %s, summary = %s,
                      gpu_device = %s, gpu_peak_vram_gb = %s, cold_start = %s,
                      progress_stage = NULL, progress_pct = 100, completed_at = now()
                WHERE id = %s AND worker_id = %s AND status = 'PROCESSING'
            RETURNING id""",
            (result_key, Jsonb(summary), gpu_device, peak_vram_gb, cold_start, job_id, worker_id),
        ).fetchone()
        if row is None:
            return False
        ledger.capture(conn, job_id, credits)
    return True


def requeue_job(conn, job_id: UUID, worker_id: str) -> bool:
    """Hand the job back (Spot interruption). Keeps the reservation; another worker will claim it."""
    return conn.execute(
        """UPDATE jobs SET status = 'UPLOADED', worker_id = NULL, progress_stage = NULL,
                           progress_pct = NULL, heartbeat_at = NULL
            WHERE id = %s AND worker_id = %s AND status = 'PROCESSING'""",
        (job_id, worker_id),
    ).rowcount == 1


def release_for_retry(conn, job_id: UUID, worker_id: str, message: str) -> bool:
    """
    Transient failure: hand the job back (UPLOADED, no owner) so SQS's redelivery can claim it at once.
    The reservation stays; after two failures the DLQ path closes the job and refunds.
    """
    return conn.execute(
        """UPDATE jobs SET status = 'UPLOADED', worker_id = NULL, heartbeat_at = NULL, progress_stage = NULL,
                           progress_pct = NULL, error_message = left(%s, 500)
            WHERE id = %s AND worker_id = %s AND status = 'PROCESSING'""",
        (message, job_id, worker_id),
    ).rowcount == 1


# ─── Anyone: end a job unsuccessfully (refunds the hold) ───────────────────


def close_job(conn, job_id: UUID, status: JobStatus, code: str, message: str = "") -> bool:
    """FAILED / EXPIRED / CANCELLED + release credits. False if already final."""
    return conn.execute(
        "SELECT close_job(%s, %s::job_status, %s, %s) AS closed", (job_id, str(status), code, message)
    ).fetchone()["closed"]


def cancel_job(conn, job_id: UUID, user_id: UUID) -> bool:
    """User cancels before uploading."""
    row = conn.execute(
        "SELECT status FROM jobs WHERE id = %s AND user_id = %s FOR UPDATE", (job_id, user_id)
    ).fetchone()
    if row is None or row["status"] != JobStatus.AWAITING_UPLOAD:
        return False
    return close_job(conn, job_id, JobStatus.CANCELLED, "CANCELLED", "Cancelled by user")


# ─── AI report (after the job has SUCCEEDED) ───────────────────────────────


def set_report_status(conn, job_id: UUID, status: str, *, key: str | None = None, error: str | None = None,
                      model: str | None = None) -> None:
    """PENDING → READY | FAILED (or OFF when no LLM is configured)."""
    done = "now()" if status in ("READY", "FAILED", "OFF") else "NULL"
    conn.execute(
        f"""UPDATE jobs SET report_status = %s, s3_report_key = COALESCE(%s, s3_report_key),
                            report_error = left(%s, 500), report_model = COALESCE(%s, report_model),
                            report_done_at = {done}
             WHERE id = %s""",
        (status, key, error, model, job_id),
    )
