#!/usr/bin/env python3
"""
NeuroLens AWS deployment (APP_ENV=aws). Four stacks in ap-southeast-1:

  NeuroLens-Network  VPC (no NAT), security groups, S3 gateway endpoint
  NeuroLens-Data     S3, SQS + DLQ, Aurora Serverless v2, ECR, SSM params   — kept on teardown
  NeuroLens-Compute  CloudFront → ALB → Fargate web; Spot GPU ASG
  NeuroLens-Ops      billing Lambdas, schedule, dashboard, alarms, FIS template, budget

Settings come from cdk.json "context" and can be overridden: cdk deploy -c worker_ami_id=ami-…
"""

import json
import shutil
from pathlib import Path

import aws_cdk as cdk

from neurolens_infra.compute_stack import ComputeStack
from neurolens_infra.data_stack import DataStack
from neurolens_infra.network_stack import NetworkStack
from neurolens_infra.ops_stack import OpsStack

ROOT = Path(__file__).resolve().parents[1]
LAMBDA_ASSET = Path(__file__).resolve().parent / "build" / "lambda"


def stage_lambda_asset() -> Path:
    """Handlers + the two shared modules they import (reaper_sql, s3keys) — boto3 is in the runtime."""
    shutil.rmtree(LAMBDA_ASSET, ignore_errors=True)
    pkg = LAMBDA_ASSET / "neurolens_common"
    pkg.mkdir(parents=True)
    for f in ("dlq_refund.py", "sla_reaper.py"):
        shutil.copy(ROOT / "lambdas" / f, LAMBDA_ASSET / f)
    for f in ("__init__.py", "reaper_sql.py", "s3keys.py"):
        shutil.copy(ROOT / "packages" / "common" / "neurolens_common" / f, pkg / f)
    return LAMBDA_ASSET


def context(app: cdk.App) -> dict:
    get = app.node.try_get_context
    as_bool = lambda v: v if isinstance(v, bool) else str(v).lower() == "true"  # noqa: E731  (-c gives strings)
    types = get("worker_instance_types")
    return {
        "region": get("region"),
        "worker_ami_id": get("worker_ami_id"),
        "worker_spot": as_bool(get("worker_spot")),
        "worker_instance_types": types.split(",") if isinstance(types, str) else types,
        "max_workers": int(get("max_workers")),
        "web_desired": int(get("web_desired")),
        "web_image_tag": get("web_image_tag"),
        "prewarm_enabled": as_bool(get("prewarm_enabled")),
        "silent_mode": get("silent_mode"),
        "idle_shutdown_seconds": int(get("idle_shutdown_seconds")),
        "aurora_min_acu": float(get("aurora_min_acu")),
        "aurora_max_acu": float(get("aurora_max_acu")),
        "budget_usd": float(get("budget_usd")),
        "alert_email": get("alert_email") or None,
        "cloudfront_prefix_list_id": get("cloudfront_prefix_list_id") or None,
        "report_llm": get("report_llm"),
        "bedrock_model": get("bedrock_model"),
        "bedrock_region": get("bedrock_region") or None,
    }


# cdk.json context as defaults: the CDK CLI also passes it (and any -c overrides, which win), and
# `python app.py` alone — used for a credential-free synth check — still gets complete settings.
DEFAULTS = json.loads((Path(__file__).resolve().parent / "cdk.json").read_text())["context"]
app = cdk.App(context=DEFAULTS)
ctx = context(app)
env = cdk.Environment(account=app.node.try_get_context("account") or None, region=ctx["region"])
tags = {"project": "neurolens", "course": "CS5224"}

net = NetworkStack(app, "NeuroLens-Network", env=env, cloudfront_prefix_list_id=ctx["cloudfront_prefix_list_id"])
data = DataStack(app, "NeuroLens-Data", env=env, vpc=net.vpc, db_sg=net.db_sg,
                 aurora_min_acu=ctx["aurora_min_acu"], aurora_max_acu=ctx["aurora_max_acu"])
compute = ComputeStack(app, "NeuroLens-Compute", env=env, net=net, data=data, ctx=ctx)
ops = OpsStack(app, "NeuroLens-Ops", env=env, data=data, compute=compute,
               lambda_asset=stage_lambda_asset(), ctx=ctx)
for stack in (net, data, compute, ops):
    for k, v in tags.items():
        cdk.Tags.of(stack).add(k, v)

app.synth()
