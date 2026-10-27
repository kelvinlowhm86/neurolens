"""
boto3 clients with the configured region.

Locally, docker-compose sets AWS_ENDPOINT_URL=http://localstack:4566, which boto3 applies to
every client — so the same code talks to LocalStack or real AWS without changes.
"""

from .config import get_settings


def client(service: str):
    import boto3

    cfg = get_settings()
    endpoint = cfg.s3_endpoint_url if service == "s3" else None
    return boto3.client(service, region_name=cfg.aws_region, endpoint_url=endpoint)
