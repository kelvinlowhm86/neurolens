"""Reaper, on Terraform's schedule (see infra/terraform/lambdas.tf).

Backstop for a lost GPU notification: a job still open past its deadline is settled if its
result exists, else refunded ``deadline_passed``. Every run also retries the file deletes of
recently failed jobs, removes shared copies that still have no retention curve (the limit comes
from config.json), finishes purging copies already marked removed or deleted, finishes deleting
marked accounts, deletes snapshots past the configured dataset age, and deletes expired sign-in
sessions.
"""

import logging
import os

from neurolens import billing, deadlines, lambdas, settings, storage

logger = logging.getLogger(__name__)

HEARTBEAT_STALE_S = deadlines.HEARTBEAT_STALE_S
FAILED_RETRY_S = deadlines.FAILED_RETRY_S


def stuck_jobs(db, deadline_s):
    with db.transaction() as tx:
        return tx.execute(
            "SELECT job_id, object_key FROM jobs WHERE "
            "created_at < now() - make_interval(secs => :deadline) AND "
            "(status = :queued OR "
            "(status = :processing AND updated_at < now() - make_interval(secs => :stale)))",
            {
                "queued": billing.QUEUED,
                "processing": billing.PROCESSING,
                "deadline": deadline_s,
                "stale": HEARTBEAT_STALE_S,
            },
        )


def recent_failed_jobs(db):
    with db.transaction() as tx:
        return tx.execute(
            "SELECT job_id, object_key FROM jobs WHERE status = :failed "
            "AND updated_at > now() - make_interval(secs => :window)",
            {"failed": billing.FAILED, "window": FAILED_RETRY_S},
        )


def expired_dataset_jobs(db, no_curve_days):
    """Shared copies past the limit that still have no retention snapshot."""
    with db.transaction() as tx:
        return tx.execute(
            "SELECT job_id FROM jobs j WHERE j.status = :done "
            "AND j.research_consent AND j.dataset_removed_at IS NULL "
            "AND j.deleted_at IS NULL "
            "AND j.created_at < now() - make_interval(days => CAST(:days AS integer)) "
            "AND NOT EXISTS "
            "(SELECT 1 FROM retention_snapshots s WHERE s.job_id = j.job_id)",
            {"done": billing.DONE, "days": no_curve_days},
        )


def unpurged_dataset_jobs(db):
    """Marked jobs (removed or deleted) whose files are still there, consented or not."""
    with db.transaction() as tx:
        return tx.execute(
            "SELECT job_id FROM jobs WHERE dataset_purged_at IS NULL "
            "AND (dataset_removed_at IS NOT NULL OR deleted_at IS NOT NULL)",
        )


def expired_sessions(db, session_days):
    """Delete session rows not used within the configured session length."""
    with db.transaction() as tx:
        tx.execute(
            "DELETE FROM sessions WHERE last_seen_at < now() "
            "- make_interval(days => CAST(:days AS integer))",
            {"days": session_days},
        )


def deleted_accounts(db):
    """The users whose account delete has marked but not yet removed."""
    with db.transaction() as tx:
        return tx.execute("SELECT user_id FROM users WHERE deleted_at IS NOT NULL")


def expired_snapshots(db, days):
    """Delete curve snapshots past the configured dataset retention."""
    with db.transaction() as tx:
        tx.execute(
            "DELETE FROM retention_snapshots WHERE added_at < now() "
            "- make_interval(days => CAST(:days AS integer))",
            {"days": days},
        )


def _delete(s3, bucket, keys):
    if not storage.delete_keys(s3, bucket, keys):
        raise RuntimeError("S3 reported a failed delete")


def _delete_job_files(s3, bucket, job_id, object_key):
    storage.delete_job_prefixes(s3, bucket, job_id)
    _delete(s3, bucket, storage.job_keys(job_id, object_key))


def reap(db, s3, bucket, job, *, deadline_s, gpu_wait_grace_s):
    job_id = job["job_id"]
    if storage.result_exists(s3, bucket, job_id):
        if billing.settle_success(db, job_id):
            logger.info(f"{job_id}: result exists, settled")
        return
    if billing.refund_if_gpu_wait_over(db, job_id, gpu_wait_grace_s=gpu_wait_grace_s):
        logger.warning(f"{job_id}: its GPU wait ran out, refunded")
        _delete_job_files(s3, bucket, job_id, job["object_key"])
        return
    if billing.issue_refund(
        db,
        job_id,
        "deadline_passed",
        queued=True,
        processing_stale_s=HEARTBEAT_STALE_S,
    ):
        logger.warning(f"{job_id}: past the {deadline_s // 3600}-hour deadline, refunded")
        # A failed delete raises after the refund, never undoing it; the run then fails loudly.
        _delete_job_files(s3, bucket, job_id, job["object_key"])


def clean_dataset(db, s3, bucket, job):
    """Mark and delete one shared copy past the limit."""
    job_id = job["job_id"]
    if not billing.mark_dataset_removed(db, job_id, "no_curve", require_no_snapshot=True):
        return
    billing.purge_job_files(db, s3, bucket, job_id)
    logger.warning(f"{job_id}: research copy deleted without a curve")


def handler(event, context):
    lambdas.setup_logging()
    import boto3

    cfg = settings.load_config()
    no_curve_days = settings.no_curve_days(cfg)
    dataset_retention_days = settings.dataset_retention_days(cfg)
    deadline_s = deadlines.job_deadline_s(cfg)
    gpu_wait_grace_s = settings.gpu_wait_grace_s(cfg)
    session_days = settings.session_days(cfg)
    db, s3 = lambdas.database(), boto3.client("s3")
    bucket = os.environ["NEUROLENS_S3_BUCKET"]
    failed = []
    for job in stuck_jobs(db, deadline_s):
        try:
            reap(
                db,
                s3,
                bucket,
                job,
                deadline_s=deadline_s,
                gpu_wait_grace_s=gpu_wait_grace_s,
            )
        except Exception:
            logger.exception(f"{job['job_id']}: could not reap")
            failed.append(job["job_id"])
    for job in recent_failed_jobs(db):
        try:
            _delete_job_files(s3, bucket, job["job_id"], job["object_key"])
        except Exception:
            logger.exception(f"{job['job_id']}: could not delete a failed job's files")
            failed.append(job["job_id"])
    for job in expired_dataset_jobs(db, no_curve_days):
        try:
            clean_dataset(db, s3, bucket, job)
        except Exception:
            logger.exception(f"{job['job_id']}: could not clean its research copy")
            failed.append(job["job_id"])
    for job in unpurged_dataset_jobs(db):
        try:
            billing.purge_job_files(db, s3, bucket, job["job_id"])
            logger.warning(f"{job['job_id']}: finished deleting its files")
        except Exception:
            logger.exception(f"{job['job_id']}: could not finish deleting its files")
            failed.append(job["job_id"])
    for user in deleted_accounts(db):
        try:
            if billing.remove_deleted_account(db, user["user_id"]):
                logger.warning(f"{user['user_id']}: finished deleting its account")
        except Exception:
            logger.exception(f"{user['user_id']}: could not finish deleting its account")
            failed.append(user["user_id"])
    try:
        expired_snapshots(db, dataset_retention_days)
    except Exception:
        logger.exception("could not delete expired snapshots")
        failed.append("snapshots")
    try:
        expired_sessions(db, session_days)
    except Exception:
        logger.exception("could not delete expired sessions")
        failed.append("sessions")
    if failed:
        raise RuntimeError(f"could not finish {failed}")
