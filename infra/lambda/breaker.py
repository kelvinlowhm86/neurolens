"""Circuit breaker (docs/M2b_spec.md §2c): stop all automatic worker launches.

Runs every 5 minutes (an EventBridge schedule). While the idle alarm is in ALARM (a worker in
service and no queue activity for 90 minutes) and no warm hold is on, it removes every worker's
scale-in protection and sets the worker group to min 0 / max 0 / desired 0, so a broken worker is
not replaced in a loop. Nothing launches again until infra/start_work.sh sets max 1.

It re-checks on every run instead of reacting once to the alarm's change, so a loop that keeps the
alarm in ALARM, or one failed run, cannot slip past it. It changes nothing when:
- the idle alarm exists and is not in ALARM (OK or INSUFFICIENT_DATA). A missing alarm counts as
  ALARM, so the breaker fails safe;
- the group's minimum is 1 or more: a warm hold, a deliberately idle worker.
Names: environment variables WORKER_GROUP and IDLE_ALARM (default neurolens-worker-idle). The
event is not read. No dependencies beyond boto3, which the Lambda runtime provides.
"""

import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    group_name = os.environ["WORKER_GROUP"]
    alarm_name = os.environ.get("IDLE_ALARM", "neurolens-worker-idle")

    alarms = boto3.client("cloudwatch").describe_alarms(AlarmNames=[alarm_name])["MetricAlarms"]
    if alarms and alarms[0]["StateValue"] != "ALARM":
        logger.info(f"{alarm_name} is {alarms[0]['StateValue']}: changing nothing")
        return {"changed": False}

    autoscaling = boto3.client("autoscaling")
    groups = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[group_name])
    if not groups["AutoScalingGroups"]:
        raise RuntimeError(f"Auto Scaling group {group_name} not found")
    group = groups["AutoScalingGroups"][0]

    if group["MinSize"] >= 1:
        logger.info(f"{group_name} has minimum {group['MinSize']} (warm hold): changing nothing")
        return {"changed": False}

    # A busy (or hung) worker protects itself from scale-in; it must go too.
    ids = [i["InstanceId"] for i in group["Instances"]]
    for start in range(0, len(ids), 50):  # the call takes at most 50 instances
        autoscaling.set_instance_protection(
            InstanceIds=ids[start : start + 50],
            AutoScalingGroupName=group_name,
            ProtectedFromScaleIn=False,
        )
    autoscaling.update_auto_scaling_group(
        AutoScalingGroupName=group_name, MinSize=0, MaxSize=0, DesiredCapacity=0
    )
    logger.warning(
        f"{alarm_name} in ALARM: {group_name} was "
        f"{group['MinSize']}/{group['MaxSize']}/{group['DesiredCapacity']} (min/max/desired); "
        f"protection removed from {len(ids)} workers, set to 0/0/0. "
        "Run infra/start_work.sh to start again."
    )
    return {"changed": True}
