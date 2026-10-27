"""
sla_reaper — EventBridge, every 5 minutes. Closes and refunds jobs that can no longer finish
(abandoned uploads, stuck workers, SLA breaches) using the shared rules in reaper_sql.py.

Talks to Aurora through the RDS Data API (no VPC, no NAT). To let Aurora pause at 0 ACU when idle,
it skips the database entirely when nothing can be live: the jobs queue is empty and no job was
created in the last hour (the web tier stamps an SSM parameter on every job creation).
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import boto3

from neurolens_common.reaper_sql import REAPER_RULES

logger = logging.getLogger()
logger.setLevel(logging.INFO)

rds = boto3.client("rds-data")
sqs = boto3.client("sqs")
ssm = boto3.client("ssm")

DB = {"resourceArn": os.environ["DB_CLUSTER_ARN"], "secretArn": os.environ["DB_SECRET_ARN"],
      "database": os.environ.get("DB_NAME", "neurolens")}
QUIET_AFTER = timedelta(minutes=60)


def _nothing_can_be_live() -> bool:
    attrs = sqs.get_queue_attributes(
        QueueUrl=os.environ["JOBS_QUEUE_URL"],
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    if int(attrs["ApproximateNumberOfMessages"]) + int(attrs["ApproximateNumberOfMessagesNotVisible"]) > 0:
        return False
    stamp = ssm.get_parameter(Name=os.environ["LAST_JOB_PARAM"])["Parameter"]["Value"]
    if stamp == "never":
        return True
    return datetime.now(timezone.utc) - datetime.fromisoformat(stamp) > QUIET_AFTER


def handler(event, context):
    if _nothing_can_be_live():
        logger.info("Idle: queue empty and no recent jobs — skipping the database")
        return {"skipped": True}
    closed = {}
    for name, sql in REAPER_RULES.items():
        r = rds.execute_statement(**DB, sql=sql)
        closed[name] = r["records"][0][0].get("longValue", 0)
    if any(closed.values()):
        logger.info("Closed jobs: %s", closed)
    return {"skipped": False, "closed": closed}
