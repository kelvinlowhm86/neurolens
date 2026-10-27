"""
Create the dev-mode AWS stand-ins in moto: bucket (+ CORS for the browser upload), jobs queue,
DLQ with the same redrive rule as production (2 receives), and the S3 → SQS upload notification.

Idempotent; run by `make dev` after moto starts (moto keeps state in memory, so every start).
"""

import json
import os

import boto3
from botocore.config import Config

REGION = os.environ.get("AWS_REGION", "ap-southeast-1")
BUCKET = os.environ["S3_BUCKET"]
ORIGIN = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")


def main() -> None:
    s3 = boto3.client("s3", region_name=REGION, config=Config(s3={"addressing_style": "path"}))
    sqs = boto3.client("sqs", region_name=REGION)

    dlq_url = sqs.create_queue(QueueName="neurolens-jobs-dlq",
                               Attributes={"MessageRetentionPeriod": "1209600"})["QueueUrl"]
    dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    q_url = sqs.create_queue(QueueName="neurolens-jobs", Attributes={
        "VisibilityTimeout": "300",
        "MessageRetentionPeriod": "86400",
        "ReceiveMessageWaitTimeSeconds": "20",
        "RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "2"}),
    })["QueueUrl"]
    q_arn = sqs.get_queue_attributes(QueueUrl=q_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]

    existing = {b["Name"] for b in s3.list_buckets().get("Buckets", [])}
    if BUCKET not in existing:
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
    s3.put_bucket_cors(Bucket=BUCKET, CORSConfiguration={"CORSRules": [{
        "AllowedOrigins": [ORIGIN], "AllowedMethods": ["POST", "GET"], "AllowedHeaders": ["*"], "MaxAgeSeconds": 3000,
    }]})
    s3.put_bucket_notification_configuration(Bucket=BUCKET, NotificationConfiguration={"QueueConfigurations": [{
        "QueueArn": q_arn, "Events": ["s3:ObjectCreated:*"],
        "Filter": {"Key": {"FilterRules": [{"Name": "prefix", "Value": "uploads/"}]}},
    }]})

    for name, want, got in (("SQS_QUEUE_URL", os.environ.get("SQS_QUEUE_URL"), q_url),
                            ("SQS_DLQ_URL", os.environ.get("SQS_DLQ_URL"), dlq_url)):
        if want != got:
            raise SystemExit(f"{name} in .env is {want!r} but moto created {got!r} — update .env")
    print(f"dev AWS ready: bucket={BUCKET} queue={q_url}")


if __name__ == "__main__":
    main()
