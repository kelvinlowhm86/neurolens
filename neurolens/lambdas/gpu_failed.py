"""SageMaker error notification handler (docs/M3e_spec.md §5).

Subscribed to the endpoint's error topic. The notification names the failed request's
``inferenceId`` (our job id). ``lambdas.finish_job`` charges a job whose result exists, else
refunds it once it is queued or its claim has gone stale and deletes its files. A fresh claim
raises, so SNS retries later; the reaper is the final backstop.
"""

import json
import logging
import os

from neurolens import lambdas

logger = logging.getLogger(__name__)


def _job_id(record):
    """The inference id from one SNS record, or None if the notification cannot be read."""
    try:
        message = json.loads(record["Sns"]["Message"])
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(message, dict):
        return None
    job_id = message.get("inferenceId")
    return job_id if isinstance(job_id, str) and job_id else None


def handler(event, context):
    lambdas.setup_logging()
    db, s3 = lambdas.database(), lambdas.s3_client()
    bucket = os.environ["NEUROLENS_S3_BUCKET"]
    waiting = []
    for record in event.get("Records", []):
        job_id = _job_id(record)
        if job_id is None:
            raw = str((record.get("Sns") or {}).get("Message"))[:2000]
            logger.warning(f"Dropped an unparseable SageMaker notification: {raw}")
            continue
        if not lambdas.finish_job(db, s3, bucket, job_id):
            waiting.append(job_id)
    if waiting:
        raise RuntimeError(f"a worker still holds {waiting}: retrying this notification later")
