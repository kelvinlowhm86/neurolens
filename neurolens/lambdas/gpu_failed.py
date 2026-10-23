"""SageMaker error notification handler (docs/M3e_spec.md §5).

Subscribed to the endpoint's error topic. The notification names the failed request's
``inferenceId`` (our job id). ``lambdas.finish_job`` charges a job whose result exists, else
refunds it once it is queued or its claim has gone stale and deletes its files. A fresh claim
raises, so SNS retries later; the reaper is the final backstop. SageMaker's own
``TestNotification``, sent when an instance starts or stops, names no request and is ignored.
"""

import json
import logging
import os

from neurolens import lambdas

logger = logging.getLogger(__name__)


def _message(record):
    """The notification of one SNS record as a dict, or None if it cannot be read."""
    try:
        message = json.loads(record["Sns"]["Message"])
    except (KeyError, TypeError, ValueError):
        return None
    return message if isinstance(message, dict) else None


def handler(event, context):
    lambdas.setup_logging()
    db, s3 = lambdas.database(), lambdas.s3_client()
    bucket = os.environ["NEUROLENS_S3_BUCKET"]
    waiting = []
    for record in event.get("Records", []):
        message = _message(record)
        if message is not None and message.get("eventName") == "TestNotification":
            logger.info(
                "Ignored SageMaker's TestNotification (sent when an instance starts or stops)"
            )
            continue
        job_id = (message or {}).get("inferenceId")
        if not (isinstance(job_id, str) and job_id):
            raw = str((record.get("Sns") or {}).get("Message"))[:2000]
            logger.warning(f"Dropped an unparseable SageMaker notification: {raw}")
            continue
        if not lambdas.finish_job(db, s3, bucket, job_id):
            waiting.append(job_id)
    if waiting:
        raise RuntimeError(f"a worker still holds {waiting}: retrying this notification later")
