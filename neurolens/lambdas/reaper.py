"""Reaper (docs/M3a_spec.md §7), every 5 minutes while a work session runs: settles or refunds
stuck jobs. Ages are measured from `updated_at`, which claim, every heartbeat and
release_for_retry refresh, so a job just handed back to the queue is not mistaken for an old one.

- processing, no heartbeat for 10 minutes: result exists -> charge; else refund as `stalled`.
- queued for 60 minutes: result exists -> charge; upload missing -> refund as
  `upload_not_received` (the 5-minute upload form expired, or the upload expired after 2 days
  before any worker took it); upload present -> leave it: it is waiting for a worker, and its
  queue message still exists (uploads expire after 2 days, messages after 4), so it runs, is
  refunded by the worker, or reaches the dead-letter handler.

Every refund re-checks its condition under the job's row lock, so a job claimed between this
query and its refund is left alone. One job's error does not stop the others; the run then
raises, so the failure shows in the Lambda's error metric.
"""

import logging
import os

from neurolens import billing, lambdas, storage

logger = logging.getLogger(__name__)

STALLED_AFTER_S = 10 * 60
QUEUED_AFTER_S = 60 * 60


def stuck_jobs(db):
    with db.transaction() as tx:
        return tx.execute(
            "SELECT job_id, status, object_key FROM jobs WHERE "
            "(status = :processing AND updated_at < now() - make_interval(secs => :stalled)) "
            "OR (status = :queued AND updated_at < now() - make_interval(secs => :queued_s))",
            {
                "processing": billing.PROCESSING,
                "queued": billing.QUEUED,
                "stalled": STALLED_AFTER_S,
                "queued_s": QUEUED_AFTER_S,
            },
        )


def reap(db, s3, bucket, job):
    job_id = job["job_id"]
    if storage.result_exists(s3, bucket, job_id):
        if billing.settle_success(db, job_id):
            logger.info(f"{job_id}: result exists, settled")
    elif job["status"] == billing.PROCESSING:
        if billing.issue_refund(db, job_id, "stalled", processing_stale_s=STALLED_AFTER_S):
            logger.warning(f"{job_id}: no heartbeat for 10 minutes, refunded")
    elif not storage.object_exists(s3, bucket, job["object_key"]):
        if billing.issue_refund(db, job_id, "upload_not_received", queued_before_s=QUEUED_AFTER_S):
            logger.warning(f"{job_id}: upload never arrived, refunded")


def handler(event, context):
    lambdas.setup_logging()
    db, s3 = lambdas.database(), lambdas.s3_client()
    bucket = os.environ["NEUROLENS_S3_BUCKET"]
    failed = []
    for job in stuck_jobs(db):
        try:
            reap(db, s3, bucket, job)
        except Exception:
            logger.exception(f"{job['job_id']}: could not reap")
            failed.append(job["job_id"])
    if failed:
        raise RuntimeError(f"could not reap {failed}")
