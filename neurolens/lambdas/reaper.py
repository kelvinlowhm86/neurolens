"""Reaper (docs/M3a §7, M3e §5, §7), every 3 hours.

Backstop for a lost GPU notification: a job still open four hours after creation is settled if
its result exists, else refunded ``deadline_passed``. Every run also retries the file deletes of
recently failed jobs and removes consented dataset copies that have no retention curve after
sixty days.
"""

import logging
import os
from pathlib import PurePosixPath

from neurolens import billing, lambdas, storage

logger = logging.getLogger(__name__)

DEADLINE_S = 4 * 60 * 60
HEARTBEAT_STALE_S = 10 * 60
FAILED_RETRY_S = 2 * 24 * 60 * 60
DATASET_WITHOUT_RETENTION_DAYS = 60


def stuck_jobs(db):
    with db.transaction() as tx:
        return tx.execute(
            "SELECT job_id, object_key FROM jobs WHERE "
            "created_at < now() - make_interval(secs => :deadline) AND "
            "(status = :queued OR "
            "(status = :processing AND updated_at < now() - make_interval(secs => :stale)))",
            {
                "queued": billing.QUEUED,
                "processing": billing.PROCESSING,
                "deadline": DEADLINE_S,
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


def expired_dataset_jobs(db):
    with db.transaction() as tx:
        return tx.execute(
            "SELECT job_id, object_key FROM jobs WHERE status = :done "
            "AND research_consent AND retention_added_at IS NULL "
            "AND dataset_removed_at IS NULL AND deleted_at IS NULL "
            "AND created_at < now() - make_interval(secs => :cutoff)",
            {"done": billing.DONE, "cutoff": DATASET_WITHOUT_RETENTION_DAYS * 86400},
        )


def _delete(s3, bucket, keys):
    if not storage.delete_keys(s3, bucket, keys):
        raise RuntimeError("S3 reported a failed delete")


def reap(db, s3, bucket, job):
    job_id = job["job_id"]
    if storage.result_exists(s3, bucket, job_id):
        if billing.settle_success(db, job_id):
            logger.info(f"{job_id}: result exists, settled")
        return
    if billing.issue_refund(
        db,
        job_id,
        "deadline_passed",
        queued=True,
        processing_stale_s=HEARTBEAT_STALE_S,
    ):
        logger.warning(f"{job_id}: past the 4-hour deadline, refunded")
        # A failed delete raises after the refund, never undoing it; the run then fails loudly.
        _delete(s3, bucket, storage.job_keys(job_id, job["object_key"]))


def _mark(db, job_id, column):
    with db.transaction() as tx:
        tx.execute(
            f"UPDATE jobs SET {column} = now() WHERE job_id = CAST(:job_id AS uuid)",
            {"job_id": job_id},
        )


def clean_dataset(db, s3, bucket, job):
    """Delete one expired consented copy, or record a retention curve that is already there."""
    job_id = job["job_id"]
    retention = f"dataset/{job_id}/retention.csv"
    if storage.object_exists(s3, bucket, retention):
        _mark(db, job_id, "retention_added_at")
        logger.info(f"{job_id}: retention curve found, kept")
        return
    ext = PurePosixPath(job["object_key"]).suffix
    keys = [
        f"dataset/{job_id}/video{ext}",
        f"dataset/{job_id}/result.json",
        f"dataset/{job_id}/brain",
    ]
    _delete(s3, bucket, keys)
    _mark(db, job_id, "dataset_removed_at")
    logger.warning(f"{job_id}: dataset copy deleted after {DATASET_WITHOUT_RETENTION_DAYS} days")


def handler(event, context):
    lambdas.setup_logging()
    import boto3

    db, s3 = lambdas.database(), boto3.client("s3")
    bucket = os.environ["NEUROLENS_S3_BUCKET"]
    failed = []
    for job in stuck_jobs(db):
        try:
            reap(db, s3, bucket, job)
        except Exception:
            logger.exception(f"{job['job_id']}: could not reap")
            failed.append(job["job_id"])
    for job in recent_failed_jobs(db):
        try:
            _delete(s3, bucket, storage.job_keys(job["job_id"], job["object_key"]))
        except Exception:
            logger.exception(f"{job['job_id']}: could not delete a failed job's files")
            failed.append(job["job_id"])
    for job in expired_dataset_jobs(db):
        try:
            clean_dataset(db, s3, bucket, job)
        except Exception:
            logger.exception(f"{job['job_id']}: could not clean its dataset copy")
            failed.append(job["job_id"])
    if failed:
        raise RuntimeError(f"could not finish {failed}")
