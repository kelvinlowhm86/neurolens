"""
Dev-mode stand-in for the two billing-integrity Lambdas:

  * `sla_reaper` (EventBridge every 5 min) → runs REAPER_RULES every 60 s
  * `dlq_refund` (SQS DLQ trigger)         → drains SQS_DLQ_URL and refunds each job

Same SQL rules as the Lambdas (reaper_sql.py), executed via psycopg.

    python -m neurolens_common.reaper_loop
"""

import logging
import time

from . import aws, db, s3keys
from .config import get_settings
from .reaper_sql import refund_from_dlq, run_rules

INTERVAL_S = 60

logger = logging.getLogger("neurolens.reaper")


def drain_dlq(sqs, dlq_url: str) -> int:
    refunded = 0
    while True:
        msgs = sqs.receive_message(QueueUrl=dlq_url, MaxNumberOfMessages=10, WaitTimeSeconds=0).get("Messages", [])
        if not msgs:
            return refunded
        for m in msgs:
            try:
                for evt in s3keys.parse_s3_event(m["Body"]):
                    with db.transaction() as conn:
                        if refund_from_dlq(conn, str(evt.job_id)):
                            refunded += 1
                            logger.info("DLQ: job %s failed twice — refunded", evt.job_id)
            except Exception:
                logger.exception("DLQ message %s could not be processed; leaving it", m["MessageId"])
                continue
            sqs.delete_message(QueueUrl=dlq_url, ReceiptHandle=m["ReceiptHandle"])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    cfg = get_settings()
    sqs = aws.client("sqs") if cfg.sqs_dlq_url else None
    logger.info("Reaper loop started (every %ss; DLQ %s)", INTERVAL_S, cfg.sqs_dlq_url or "off")
    while True:
        try:
            with db.transaction() as conn:
                closed = run_rules(conn)
            if any(closed.values()):
                logger.info("Reaper closed jobs: %s", closed)
            if sqs:
                drain_dlq(sqs, cfg.sqs_dlq_url)
        except Exception:
            logger.exception("Reaper pass failed")
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
