"""The dispatcher Lambda (docs/M3e_spec.md §2, §3).

SQS event source on the jobs queue, batch size 1. For each uploaded file it checks the size,
measures the duration with ffprobe and prices the job through ``billing.verify_queued`` before
calling SageMaker's asynchronous endpoint. A bad file or short credit is refunded before any GPU
wakes.
"""

import logging
import os
from datetime import UTC, datetime

from botocore.exceptions import ClientError

from neurolens import billing, lambdas, scaling, storage
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
    appscaling,
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

    if scaling.endpoint_paused(appscaling, endpoint):
        return _refund(db, s3, bucket, key, job_id, "processing_paused")

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


def start_gpu_if_off(control, endpoint):
    """Start the endpoint after a dispatch (M3f §6a). Returns the outcome; never raises."""
    try:
        variants = control.describe_endpoint(EndpointName=endpoint).get("ProductionVariants", [])
    except Exception:
        logger.exception(f"Could not describe {endpoint}; the GPU was not started")
        return "error"
    if len(variants) != 1:
        logger.error(f"{endpoint} has {len(variants)} production variants; expected one")
        return "error"

    variant = variants[0]
    if variant.get("DesiredInstanceCount", 0) > 0:
        logger.info(f"GPU already running for {endpoint}")
        return "running"

    try:
        control.update_endpoint_weights_and_capacities(
            EndpointName=endpoint,
            DesiredWeightsAndCapacities=[
                {"VariantName": variant["VariantName"], "DesiredInstanceCount": 1}
            ],
        )
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ValidationException":
            logger.info(f"GPU start already under way for {endpoint}")
            return "starting"
        logger.exception(f"Could not start the GPU for {endpoint}")
        return "error"
    except Exception:
        logger.exception(f"Could not start the GPU for {endpoint}")
        return "error"
    logger.info(f"Started the GPU for {endpoint}")
    return "started"


def handler(event, context):
    lambdas.setup_logging()
    s3, db = lambdas.s3_client(), lambdas.database()
    sagemaker = lambdas.sagemaker_runtime_client()
    appscaling = lambdas.appscaling_client()
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
                    appscaling=appscaling,
                    db=db,
                    endpoint=endpoint,
                    max_upload_bytes=max_upload_bytes,
                    max_duration_s=max_duration_s,
                )
            )
    if "invoked" in results:
        start_gpu_if_off(lambdas.sagemaker_client(), endpoint)
    return results
