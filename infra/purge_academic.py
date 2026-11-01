"""Remove the old course-study copies under academic/ from the configured bucket (M3j §9b).

    AWS_PROFILE=neurolens python infra/purge_academic.py            # count only, deletes nothing
    AWS_PROFILE=neurolens python infra/purge_academic.py --delete   # remove every academic/ key

Dry run is the default. It only ever lists and deletes the `academic/` prefix in the configured
bucket (`NEUROLENS_S3_BUCKET`, else Terraform's bucket output), paging through the listing. Run it
after the worker image that stops writing these copies is live; then remove `academic/` from
`storage.delete_job_prefixes`, the lifecycle rule and IAM in one Terraform apply.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "academic/"


def bucket_name():
    from_env = os.environ.get("NEUROLENS_S3_BUCKET")
    if from_env:
        return from_env
    return subprocess.check_output(
        ["terraform", "-chdir=infra/terraform", "output", "-raw", "bucket_name"],
        cwd=ROOT,
        text=True,
    ).strip()


def _client():
    import boto3

    region = os.environ.get("NEUROLENS_AWS_REGION")
    return boto3.client("s3", region_name=region) if region else boto3.client("s3")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--delete",
        action="store_true",
        help="delete the listed keys (without it, only count them)",
    )
    args = parser.parse_args(argv)

    bucket = bucket_name()
    s3 = _client()
    keys = [
        item["Key"]
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=PREFIX)
        for item in page.get("Contents", [])
    ]
    if not args.delete:
        print(f"{len(keys)} objects under {PREFIX} in {bucket}; dry run, nothing deleted.")
        return 0

    for start in range(0, len(keys), 1000):
        batch = keys[start : start + 1000]
        reported = s3.delete_objects(
            Bucket=bucket, Delete={"Objects": [{"Key": key} for key in batch]}
        ).get("Errors")
        if reported:
            print(
                f"S3 reported {len(reported)} failed deletes under {PREFIX} in {bucket}.",
                file=sys.stderr,
            )
            return 1
    print(f"Deleted {len(keys)} objects under {PREFIX} from {bucket}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
