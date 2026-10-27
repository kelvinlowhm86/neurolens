"""
External services the web tier talks to, bundled so tests can swap in fakes.

`storage` is S3 (presigned URLs + reading results); `asg` and `ssm` are only used on AWS.
"""

import json
import time
from dataclasses import dataclass, field

from neurolens_common import aws
from neurolens_common.config import Settings


class Storage:
    def __init__(self, cfg: Settings, internal_client=None, presign_client=None):
        self.cfg = cfg
        self.bucket = cfg.s3_bucket
        self.s3 = internal_client
        self.presign = presign_client or internal_client

    @classmethod
    def from_settings(cls, cfg: Settings) -> "Storage":
        import boto3
        from botocore.config import Config

        # Path-style for LocalStack; SigV4 everywhere so presigned URLs work in every region.
        bcfg = Config(signature_version="s3v4",
                      s3={"addressing_style": "path" if cfg.s3_public_endpoint_url else "auto"})
        presign = boto3.client("s3", region_name=cfg.aws_region, config=bcfg,
                               endpoint_url=cfg.s3_public_endpoint_url)
        return cls(cfg, aws.client("s3"), presign)

    def presigned_upload(self, key: str, content_type: str, expires_s: int = 900) -> dict:
        """Browser POSTs the file straight to S3. S3 itself enforces the size limit and type."""
        return self.presign.generate_presigned_post(
            Bucket=self.bucket, Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, self.cfg.max_upload_bytes]],
            ExpiresIn=expires_s,
        )

    def presigned_get(self, key: str, expires_s: int = 3600) -> str:
        return self.presign.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_s,
        )

    def read_json(self, key: str) -> dict:
        return json.loads(self.read_bytes(key))

    def read_bytes(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()


@dataclass
class Services:
    storage: Storage
    asg: object | None = None
    ssm: object | None = None
    _asg_cache: dict = field(default_factory=dict)

    @classmethod
    def from_settings(cls, cfg: Settings) -> "Services":
        on_aws = cfg.app_env == "aws"
        return cls(
            storage=Storage.from_settings(cfg),
            asg=aws.client("autoscaling") if on_aws and cfg.asg_name else None,
            ssm=aws.client("ssm") if on_aws and cfg.last_job_param else None,
        )

    def describe_asg(self, name: str, ttl_s: float = 10) -> dict | None:
        """ASG state, cached briefly so 5-second polling doesn't hammer the API."""
        if self.asg is None:
            return None
        hit = self._asg_cache.get(name)
        if hit and time.monotonic() - hit[0] < ttl_s:
            return hit[1]
        groups = self.asg.describe_auto_scaling_groups(AutoScalingGroupNames=[name])["AutoScalingGroups"]
        g = groups[0] if groups else None
        self._asg_cache[name] = (time.monotonic(), g)
        return g
