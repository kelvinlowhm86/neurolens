"""Shared Lambda helpers (docs/M3a §7, M3e §3, §5, §6c).

Pure Python plus boto3 (the Lambda runtime provides it): no numpy, no psycopg. Configuration
comes from environment variables set by Terraform, named as everywhere else.
"""

import logging
import os

from neurolens import billing, storage
from neurolens import db as dbmod
from neurolens.billing import STALE_AFTER_S

logger = logging.getLogger("neurolens")


def setup_logging():
    """Lambda's root logger writes to CloudWatch; INFO keeps each decision in the log."""
    logging.getLogger().setLevel(logging.INFO)


def database():
    """Aurora through the Data API, waking it if paused (the default 60 s budget)."""
    return dbmod.DataApiDatabase(
        dbmod.data_api_client(),
        os.environ["NEUROLENS_DB_CLUSTER_ARN"],
        os.environ["NEUROLENS_DB_SECRET_ARN"],
        os.environ["NEUROLENS_DB_NAME"],
    )


def s3_client():
    import boto3

    return boto3.client("s3")


def sagemaker_runtime_client():
    """Sends requests to the endpoint (InvokeEndpointAsync): the dispatcher."""
    import boto3

    return boto3.client("sagemaker-runtime")


def sagemaker_client():
    """Reads and resizes the endpoint (DescribeEndpoint, capacity): the GPU guard."""
    import boto3

    return boto3.client("sagemaker")


def appscaling_client():
    import boto3

    return boto3.client("application-autoscaling")


def finish_job(db, s3, bucket, job_id):
    """True when the job is finished (charged, refunded or already terminal); False while a
    live worker holds it (M3e §5).

    A result means the job is settled; otherwise a queued or stale job is refunded
    ``processing_failed`` and every file of the job is deleted. A delete that fails raises after
    the refund, never undoing it.
    """
    state = None if job_id is None else billing.job_state(db, job_id)
    if state is None or state["status"] in billing.TERMINAL:
        logger.info(f"{job_id}: unknown or already finished, nothing to do")
        return True
    if storage.result_exists(s3, bucket, job_id):
        billing.settle_success(db, job_id)
        logger.info(f"{job_id}: result exists, settled")
        return True
    message = _last_error(db, job_id)
    if billing.issue_refund(
        db,
        job_id,
        "processing_failed",
        message,
        queued=True,
        processing_stale_s=STALE_AFTER_S,
    ):
        logger.warning(f"{job_id}: failed on the GPU, refunded")
        # A failed delete raises after the refund, never undoing it; the run then fails loudly.
        if not storage.delete_keys(s3, bucket, storage.job_keys(job_id, state["object_key"])):
            raise RuntimeError(f"{job_id}: refunded, but S3 could not delete all of its files")
        return True
    state = billing.job_state(db, job_id)
    return state is not None and state["status"] in billing.TERMINAL


def _last_error(db, job_id):
    with db.transaction() as tx:
        rows = tx.execute(
            "SELECT error_message FROM jobs WHERE job_id = CAST(:job_id AS uuid)",
            {"job_id": job_id},
        )
    return rows[0]["error_message"] if rows else None
