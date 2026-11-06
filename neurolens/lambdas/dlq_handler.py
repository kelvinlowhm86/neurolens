"""Dead-letter handler (docs/M3a_spec.md §7): settles or refunds every job that failed twice.

SQS event source on the dead-letter queue, batch size 1. Each job is finished by
`lambdas.finish_job`: a result means it is charged, otherwise a queued or stale job is refunded
`processing_failed` and its files deleted. A job whose worker still holds a fresh claim is left
alone and the handler raises, so SQS retries the message later.
"""

import logging

from neurolens import lambdas, storage

logger = logging.getLogger(__name__)


def handler(event, context):
    lambdas.setup_logging()
    db, s3 = lambdas.database(), lambdas.s3_client()
    waiting, failed = [], []
    for record in event["Records"]:
        for bucket, key in storage.parse_s3_event(record["body"]):
            job_id = storage.job_id_from_key(key)
            try:
                if not lambdas.finish_job(db, s3, bucket, job_id):
                    waiting.append(job_id)
            except Exception:
                logger.exception(f"{job_id}: could not finish")
                failed.append(job_id)
    if waiting:
        raise RuntimeError(f"a worker still holds {waiting}: retrying this message later")
    if failed:
        raise RuntimeError(f"could not finish {failed}")
