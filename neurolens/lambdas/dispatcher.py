"""The dispatcher Lambda (docs/M3e_spec.md §2, §3).

SQS event source on the jobs queue, batch size 1. For each uploaded file it checks the size,
measures the duration with ffprobe and prices the job through ``billing.verify_queued`` before
calling SageMaker's asynchronous endpoint. A bad file or short credit is refunded before any GPU
wakes.
"""

import logging
import os
from datetime import UTC, datetime

from neurolens import billing, lambdas, storage
from neurolens.media import UnreadableVideo, probe_duration

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_S = 90
QUEUE_TTL_MAX_S = 7200
DEADLINE_S = 10800  # the reaper's 4-hour bound minus the 60-minute invocation timeout
MIN_TTL_S = 300
INVOCATION_TIMEOUT_S = 3600
PRESIGNED_GET_S = 600


def _created_at(db, job_id):
    with db.transaction() as tx:
        rows = tx.execute(
            "SELECT created_at FROM jobs WHERE job_id = CAST(:job_id AS uuid)",
            {"job_id": job_id},
        )
    return rows[0]["created_at"] if rows else None


def _delete_upload(s3, bucket, key):
    s3.delete_object(Bucket=bucket, Key=key)


def _refund(db, s3, bucket, key, job_id, reason, *, delete=True):
    """Refund a queued job; delete the upload only when the refund really happened."""
    if not billing.issue_refund(db, job_id, reason, queued=True):
        return "skipped"
    logger.warning(f"Refunded {job_id}: {reason}")
    if delete:
        _delete_upload(s3, bucket, key)
    return "refunded"


def dispatch(
    bucket,
    key,
    *,
    s3,
    sagemaker,
    db,
    endpoint,
    max_upload_bytes,
    max_duration_s,
):
    """Check, price and dispatch one upload. Returns "invoked", "skipped" or "refunded"."""
    job_id = storage.job_id_from_key(key)
    state = billing.job_state(db, job_id) if job_id is not None else None
    if state is None:
        logger.info(f"Skipped {key}: no job in the database")
        return "skipped"

    if state["status"] in billing.TERMINAL:
        if state["status"] == billing.FAILED:
            # A refund deleted the upload; this copy landed afterwards (M3a §7b).
            logger.info(f"Skipped {job_id}: already refunded, deleting the late upload")
            _delete_upload(s3, bucket, key)
        else:
            logger.info(f"Skipped {job_id}: already finished")
        return "skipped"
    if state["status"] == billing.PROCESSING:
        logger.info(f"Skipped {job_id}: already running")
        return "skipped"

    try:
        size = s3.head_object(Bucket=bucket, Key=key)["ContentLength"]
    except Exception as err:
        if storage.is_not_found(err):
            return _refund(db, s3, bucket, key, job_id, "upload_missing", delete=False)
        raise
    if size > max_upload_bytes:
        return _refund(db, s3, bucket, key, job_id, "file_too_large")

    url = storage.presigned_get(
        s3, bucket, key, PRESIGNED_GET_S, unique_id="neurolens-dispatcher-sigv4"
    )
    try:
        duration = probe_duration(url, timeout_s=PROBE_TIMEOUT_S)
    except UnreadableVideo:
        return _refund(db, s3, bucket, key, job_id, "unreadable_video")

    verdict = billing.verify_queued(db, job_id, round(duration * 1000), max_duration_s)
    if verdict == "not_queued":
        logger.info(f"Skipped {job_id}: no longer queued")
        return "skipped"
    if verdict != billing.OK:
        # verify_queued already refunded the job (too long or short credit).
        logger.warning(f"Refunded {job_id}: {verdict}")
        _delete_upload(s3, bucket, key)
        return "refunded"

    created = _created_at(db, job_id)
    age = (datetime.now(UTC) - created).total_seconds() if created is not None else DEADLINE_S
    ttl = min(QUEUE_TTL_MAX_S, DEADLINE_S - age)
    if ttl < MIN_TTL_S:
        return _refund(db, s3, bucket, key, job_id, "deadline_passed")

    sagemaker.invoke_endpoint_async(
        EndpointName=endpoint,
        InputLocation=f"s3://{bucket}/{key}",
        InferenceId=job_id,
        CustomAttributes=job_id,
        ContentType="application/octet-stream",
        RequestTTLSeconds=int(ttl),
        InvocationTimeoutSeconds=INVOCATION_TIMEOUT_S,
    )
    logger.info(f"Invoked {job_id} with a {int(ttl)}s queue TTL")
    return "invoked"


def handler(event, context):
    lambdas.setup_logging()
    s3, db = lambdas.s3_client(), lambdas.database()
    sagemaker = lambdas.sagemaker_runtime_client()
    endpoint = os.environ["NEUROLENS_GPU_ENDPOINT"]
    max_upload_bytes = int(os.environ["NEUROLENS_MAX_UPLOAD_BYTES"])
    max_duration_s = int(os.environ["NEUROLENS_MAX_DURATION_SECONDS"])
    results = []
    for record in event["Records"]:
        for bucket, key in storage.parse_s3_event(record["body"]):
            results.append(
                dispatch(
                    bucket,
                    key,
                    s3=s3,
                    sagemaker=sagemaker,
                    db=db,
                    endpoint=endpoint,
                    max_upload_bytes=max_upload_bytes,
                    max_duration_s=max_duration_s,
                )
            )
    return results
