"""Circuit breaker (docs/M2b_spec.md §2c): stop all automatic worker launches.

EventBridge calls this when the idle alarm (a worker in service and no queue activity for 90
minutes) goes to ALARM. It sets the worker group to min 0 / max 0 / desired 0, so nothing
launches again until infra/start_work.sh sets max 1. A warm hold (minimum 1 or more) is a
deliberately idle worker, so then it changes nothing. Group name: environment variable
WORKER_GROUP. No dependencies beyond boto3, which the Lambda runtime provides.
"""

import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    group_name = os.environ["WORKER_GROUP"]
    autoscaling = boto3.client("autoscaling")
    groups = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[group_name])
    if not groups["AutoScalingGroups"]:
        raise RuntimeError(f"Auto Scaling group {group_name} not found")
    group = groups["AutoScalingGroups"][0]

    if group["MinSize"] >= 1:
        logger.info(f"{group_name} has minimum {group['MinSize']} (warm hold): changing nothing")
        return {"changed": False}

    autoscaling.update_auto_scaling_group(
        AutoScalingGroupName=group_name, MinSize=0, MaxSize=0, DesiredCapacity=0
    )
    logger.warning(
        f"{group_name} was {group['MinSize']}/{group['MaxSize']}/{group['DesiredCapacity']} "
        "(min/max/desired); set to 0/0/0. Run infra/start_work.sh to start again."
    )
    return {"changed": True}
