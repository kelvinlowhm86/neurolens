"""The M3a Lambdas (docs/M3a_spec.md §7): the dead-letter handler and the reaper.

Pure Python plus boto3 (the Lambda runtime provides it): no numpy, no psycopg. Configuration
comes from environment variables set by Terraform, named as everywhere else.
"""

import logging
import os

from neurolens import db as dbmod


def setup_logging():
    """Lambda's root logger writes to CloudWatch; INFO keeps each decision in the log."""
    logging.getLogger().setLevel(logging.INFO)


def database():
    """Aurora through the Data API, waking it if paused (the default 60 s budget)."""
    import boto3

    return dbmod.DataApiDatabase(
        boto3.client("rds-data"),
        os.environ["NEUROLENS_DB_CLUSTER_ARN"],
        os.environ["NEUROLENS_DB_SECRET_ARN"],
        os.environ["NEUROLENS_DB_NAME"],
    )


def s3_client():
    import boto3

    return boto3.client("s3")
