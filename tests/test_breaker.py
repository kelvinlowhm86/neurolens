"""The circuit breaker Lambda, infra/lambda/breaker.py (moto Auto Scaling). Written from
docs/M2b_spec.md §1a and §2c: with the group's minimum at 0 it sets min 0 / max 0 / desired 0;
during a warm hold (minimum 1 or more) it changes nothing. infra/lambda is not a package, so the
module is loaded from its path."""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BREAKER = REPO_ROOT / "infra" / "lambda" / "breaker.py"
GROUP = "neurolens-workers"

# What EventBridge passes when the idle alarm changes to ALARM (abridged).
ALARM_EVENT = {
    "version": "0",
    "source": "aws.cloudwatch",
    "detail-type": "CloudWatch Alarm State Change",
    "region": "us-east-1",
    "detail": {
        "alarmName": "neurolens-worker-idle",
        "state": {"value": "ALARM"},
        "previousState": {"value": "OK"},
    },
}


@pytest.fixture
def asg(monkeypatch):
    """A moto Auto Scaling group named like the real one; returns (client, make_group)."""
    import boto3
    from moto import mock_aws

    monkeypatch.setenv("WORKER_GROUP", GROUP)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    with mock_aws():
        ec2 = boto3.client("ec2", region_name="us-east-1")
        client = boto3.client("autoscaling", region_name="us-east-1")
        ami = ec2.describe_images()["Images"][0]["ImageId"]
        ec2.create_launch_template(
            LaunchTemplateName="neurolens-worker",
            LaunchTemplateData={"ImageId": ami, "InstanceType": "t3.large"},
        )

        def make_group(min_size, max_size, desired):
            client.create_auto_scaling_group(
                AutoScalingGroupName=GROUP,
                LaunchTemplate={"LaunchTemplateName": "neurolens-worker", "Version": "$Latest"},
                MinSize=min_size,
                MaxSize=max_size,
                DesiredCapacity=desired,
                AvailabilityZones=["us-east-1a"],
            )

        yield client, make_group


def load_breaker():
    spec = importlib.util.spec_from_file_location("neurolens_breaker_under_test", BREAKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sizes(client):
    group = client.describe_auto_scaling_groups(AutoScalingGroupNames=[GROUP])["AutoScalingGroups"][
        0
    ]
    return group["MinSize"], group["MaxSize"], group["DesiredCapacity"]


@pytest.mark.parametrize("start", [(0, 1, 0), (0, 1, 1), (0, 2, 2)], ids=str)
def test_with_minimum_zero_the_group_ends_at_zero_zero_zero(asg, start):
    client, make_group = asg
    make_group(*start)
    load_breaker().handler(ALARM_EVENT, None)
    assert sizes(client) == (0, 0, 0)


@pytest.mark.parametrize("start", [(1, 1, 1), (1, 2, 1)], ids=str)
def test_during_a_warm_hold_the_group_is_unchanged(asg, start):
    client, make_group = asg
    make_group(*start)
    load_breaker().handler(ALARM_EVENT, None)
    assert sizes(client) == start


def test_the_group_name_comes_from_worker_group(asg, monkeypatch):
    """Only the group named by WORKER_GROUP is touched."""
    import boto3

    client, make_group = asg
    make_group(0, 1, 0)
    ec2_group = "neurolens-other-group"
    client.create_auto_scaling_group(
        AutoScalingGroupName=ec2_group,
        LaunchTemplate={"LaunchTemplateName": "neurolens-worker", "Version": "$Latest"},
        MinSize=0,
        MaxSize=1,
        DesiredCapacity=0,
        AvailabilityZones=["us-east-1a"],
    )
    load_breaker().handler(ALARM_EVENT, None)
    assert sizes(client) == (0, 0, 0)
    other = boto3.client("autoscaling", region_name="us-east-1").describe_auto_scaling_groups(
        AutoScalingGroupNames=[ec2_group]
    )["AutoScalingGroups"][0]
    assert (other["MinSize"], other["MaxSize"], other["DesiredCapacity"]) == (0, 1, 0)
