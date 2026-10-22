"""Dead-letter handler (docs/M3a_spec.md §7): settles or refunds every job that failed twice.

SQS event source on the dead-letter queue, batch size 1. For each job in the S3 event:
- unknown, or already done/failed: nothing to do;
- its result exists (a worker crashed before settling): charge it;
- otherwise refund it as `processing_failed` with the job's last error message, if it is queued
  or its worker has stopped heartbeating (stale for 90 s), then delete every file of the job
  (M3a §7b).

A job whose worker still holds a fresh claim (a duplicate message for a job still running) is
left alone and the handler raises once every job has been seen, so SQS retries the message
later, by which time the job has finished or gone stale. It returns normally only when every job
is settled, refunded or already terminal. A failed file delete is logged and also makes the
handler raise, but never undoes the refund.
"""

import logging

from neurolens import billing, lambdas, storage

logger = logging.getLogger(__name__)

STALE_AFTER_S = 90  # as billing.claim: a live worker refreshes its claim every <= 50 s


def _last_error_and_key(db, job_id):
    with db.transaction() as tx:
        rows = tx.execute(
            "SELECT error_message, object_key FROM jobs WHERE job_id = CAST(:job_id AS uuid)",
            {"job_id": job_id},
        )
    return (rows[0]["error_message"], rows[0]["object_key"]) if rows else (None, None)


def settle_or_refund(db, s3, bucket, job_id):
    """True when the job is finished (charged, refunded or already terminal); False while a
    live worker holds it."""
    state = None if job_id is None else billing.job_state(db, job_id)
    if state is None or state["status"] in billing.TERMINAL:
        logger.info(f"{job_id}: unknown or already finished, nothing to do")
        return True
    if storage.result_exists(s3, bucket, job_id):
        billing.settle_success(db, job_id)
        logger.info(f"{job_id}: result exists, settled")
        return True
    message, object_key = _last_error_and_key(db, job_id)
    if billing.issue_refund(
        db,
        job_id,
        "processing_failed",
        message,
        queued_before_s=0,
        processing_stale_s=STALE_AFTER_S,
    ):
        logger.warning(f"{job_id}: failed twice, refunded")
        # A failed delete raises after the refund, never undoing it; the run then fails loudly.
        if not storage.delete_keys(s3, bucket, storage.job_keys(job_id, object_key)):
            raise RuntimeError(f"{job_id}: refunded, but S3 could not delete all of its files")
        return True
    state = billing.job_state(db, job_id)
    return state is not None and state["status"] in billing.TERMINAL


def handler(event, context):
    lambdas.setup_logging()
    db, s3 = lambdas.database(), lambdas.s3_client()
    waiting, failed = [], []
    for record in event["Records"]:
        for bucket, key in storage.parse_s3_event(record["body"]):
            job_id = storage.job_id_from_key(key)
            try:
                if not settle_or_refund(db, s3, bucket, job_id):
                    waiting.append(job_id)
            except Exception:
                logger.exception(f"{job_id}: could not finish")
                failed.append(job_id)
    if waiting:
        raise RuntimeError(f"a worker still holds {waiting}: retrying this message later")
    if failed:
        raise RuntimeError(f"could not finish {failed}")
