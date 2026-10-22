"""Reaper (docs/M3a_spec.md §7), every 3 hours, always on: settles or refunds any job still
open 24 hours after it was created, unless a worker is running it.

One rule: a job not done or failed 24 hours after `created_at`, and that no worker is running
(it is `queued`, or `processing` with no heartbeat for 10 minutes), is settled if its result
exists, else refunded as `deadline_passed` and its upload deleted (M3a §7b).

Every refund re-checks its condition under the job's row lock, so a job claimed between this
query and its refund is left alone. One job's error, including a failed upload delete after a
refund, does not stop the others; the run then raises, so the failure shows in the Lambda's
error metric.
"""

import logging
import os

from neurolens import billing, lambdas, storage

logger = logging.getLogger(__name__)

DEADLINE_S = 24 * 60 * 60
HEARTBEAT_STALE_S = 10 * 60


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
        queued_before_s=0,
        processing_stale_s=HEARTBEAT_STALE_S,
    ):
        logger.warning(f"{job_id}: past the 24-hour deadline, refunded")
        storage.delete_object(s3, bucket, job["object_key"])


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
