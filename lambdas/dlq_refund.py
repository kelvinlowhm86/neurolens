"""
dlq_refund — triggered by the jobs DLQ. A message lands there after two genuine processing failures
(Spot interruptions don't count: the worker re-sends those as fresh messages). Each job is marked
FAILED and its credit hold released, via the shared DLQ_REFUND statement (idempotent).
"""

import logging
import os

import boto3

from neurolens_common.reaper_sql import DLQ_REFUND
from neurolens_common.s3keys import parse_s3_event

logger = logging.getLogger()
logger.setLevel(logging.INFO)

rds = boto3.client("rds-data")
DB = {"resourceArn": os.environ["DB_CLUSTER_ARN"], "secretArn": os.environ["DB_SECRET_ARN"],
      "database": os.environ.get("DB_NAME", "neurolens")}


def handler(event, context):
    failures = []
    for rec in event.get("Records", []):
        try:
            for evt in parse_s3_event(rec["body"]):
                r = rds.execute_statement(**DB, sql=DLQ_REFUND, parameters=[
                    {"name": "job_id", "value": {"stringValue": str(evt.job_id)}},
                ])
                refunded = r["records"][0][0].get("booleanValue", False)
                logger.info("Job %s: %s", evt.job_id, "failed + refunded" if refunded else "already final")
        except Exception:
            logger.exception("Could not process DLQ message %s", rec.get("messageId"))
            failures.append({"itemIdentifier": rec["messageId"]})
    # Partial batch response: only failed messages return to the DLQ.
    return {"batchItemFailures": failures}
