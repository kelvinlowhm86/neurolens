"""The scheduled circuit breaker and the idle alarm, infra/lambda/breaker.py. Written first from
docs/M2b_spec.md §1a (`handler`) and §2c, §11: the breaker runs every 5 minutes; if the idle alarm
exists and is not in ALARM it changes nothing (a missing alarm counts as ALARM, so it fails safe);
during a warm hold (minimum 1 or more) it changes nothing; otherwise it removes every instance's
scale-in protection, then sets the group to 0 / 0 / 0.

moto (Auto Scaling, CloudWatch, EC2) models scale-in protection like AWS: lowering the desired
capacity does not end a protected instance. So "protection removed BEFORE the group is set to 0"
is checked by its effect (no worker is left in the group), with no call-order recording. If the
breaker set 0/0/0 first, the protected workers would stay in service.

tests/test_breaker.py (no alarm created) stays valid: a missing alarm counts as ALARM.
"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BREAKER = REPO_ROOT / "infra" / "lambda" / "breaker.py"
GROUP = "neurolens-workers"
DEFAULT_ALARM = "neurolens-worker-idle"

# What the 5-minute EventBridge schedule passes (abridged). §1a: the event is not read.
SCHEDULED_EVENT = {
    "version": "0",
    "source": "aws.events",
    "detail-type": "Scheduled Event",
    "region": "us-east-1",
    "detail": {},
}


@pytest.fixture
def env(monkeypatch):
    """moto Auto Scaling, CloudWatch and EC2 with a launch template; WORKER_GROUP set and
    IDLE_ALARM unset (the default name applies unless a test sets it)."""
    import boto3
    from moto import mock_aws

    monkeypatch.setenv("WORKER_GROUP", GROUP)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("IDLE_ALARM", raising=False)
    with mock_aws():
        ec2 = boto3.client("ec2", region_name="us-east-1")
        ami = ec2.describe_images()["Images"][0]["ImageId"]
        ec2.create_launch_template(
            LaunchTemplateName="neurolens-worker",
            LaunchTemplateData={"ImageId": ami, "InstanceType": "t3.large"},
        )
        yield Env(
            boto3.client("autoscaling", region_name="us-east-1"),
            boto3.client("cloudwatch", region_name="us-east-1"),
        )


class Env:
    def __init__(self, autoscaling, cloudwatch):
        self.autoscaling = autoscaling
        self.cloudwatch = cloudwatch

    def make_group(self, min_size, max_size, desired, *, protect=True):
        """The worker group; its running instances are protected from scale-in (each holds a
        job) unless protect=False."""
        self.autoscaling.create_auto_scaling_group(
            AutoScalingGroupName=GROUP,
            LaunchTemplate={"LaunchTemplateName": "neurolens-worker", "Version": "$Latest"},
            MinSize=min_size,
            MaxSize=max_size,
            DesiredCapacity=desired,
            AvailabilityZones=["us-east-1a"],
        )
        ids = self.instance_ids()
        assert len(ids) == desired
        if protect and ids:
            self.autoscaling.set_instance_protection(
                InstanceIds=ids, AutoScalingGroupName=GROUP, ProtectedFromScaleIn=True
            )
        return ids

    def make_alarm(self, state, name=DEFAULT_ALARM):
        self.cloudwatch.put_metric_alarm(
            AlarmName=name,
            Namespace="AWS/SQS",
            MetricName="NumberOfMessagesReceived",
            Statistic="Sum",
            Period=60,
            EvaluationPeriods=90,
            Threshold=0,
            ComparisonOperator="LessThanOrEqualToThreshold",
        )
        self.cloudwatch.set_alarm_state(AlarmName=name, StateValue=state, StateReason="test")

    def group(self):
        return self.autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[GROUP])[
            "AutoScalingGroups"
        ][0]

    def sizes(self):
        g = self.group()
        return g["MinSize"], g["MaxSize"], g["DesiredCapacity"]

    def instance_ids(self):
        return [i["InstanceId"] for i in self.group()["Instances"]]

    def protection(self):
        return {i["InstanceId"]: i["ProtectedFromScaleIn"] for i in self.group()["Instances"]}


def run_breaker():
    spec = importlib.util.spec_from_file_location("neurolens_breaker_alarm_under_test", BREAKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.handler(SCHEDULED_EVENT, None)


# ---------------------------------------------------------------- alarm not in ALARM: nothing


@pytest.mark.parametrize("state", ["OK", "INSUFFICIENT_DATA"])
@pytest.mark.parametrize("start", [(0, 1, 1), (0, 2, 2), (1, 1, 1)], ids=str)
def test_alarm_present_and_not_in_alarm_changes_nothing(env, state, start):
    ids = env.make_group(*start)
    env.make_alarm(state)
    run_breaker()
    assert env.sizes() == start
    assert env.protection() == {i: True for i in ids}


# ---------------------------------------------------------------- alarm in ALARM (or absent)


@pytest.mark.parametrize("start", [(0, 1, 1), (0, 2, 2)], ids=str)
def test_alarm_in_alarm_with_minimum_zero_removes_protection_and_stops_the_group(env, start):
    env.make_group(*start)
    env.make_alarm("ALARM")
    run_breaker()
    assert env.sizes() == (0, 0, 0)
    # Every worker was protected: they can only be gone if protection was removed before (or
    # while) the group was set to 0. A protected worker left in service would keep billing.
    assert env.instance_ids() == []


@pytest.mark.parametrize("start", [(0, 1, 1), (0, 2, 2)], ids=str)
def test_missing_alarm_counts_as_alarm_and_stops_the_group(env, start):
    env.make_group(*start)  # no alarm created at all
    run_breaker()
    assert env.sizes() == (0, 0, 0)
    assert env.instance_ids() == []


def test_alarm_in_alarm_with_an_empty_group_sets_zero_zero_zero(env):
    env.make_group(0, 1, 0)
    env.make_alarm("ALARM")
    run_breaker()
    assert env.sizes() == (0, 0, 0)


@pytest.mark.parametrize("start", [(1, 1, 1), (1, 2, 2)], ids=str)
def test_alarm_in_alarm_during_a_warm_hold_changes_nothing_and_keeps_protection(env, start):
    ids = env.make_group(*start)
    env.make_alarm("ALARM")
    run_breaker()
    assert env.sizes() == start
    assert env.protection() == {i: True for i in ids}


# ---------------------------------------------------------------- the alarm's name


def test_alarm_name_comes_from_idle_alarm(env, monkeypatch):
    """IDLE_ALARM names the alarm read: its OK state wins over an ALARM under the default name."""
    monkeypatch.setenv("IDLE_ALARM", "neurolens-worker-idle-custom")
    ids = env.make_group(0, 1, 1)
    env.make_alarm("OK", name="neurolens-worker-idle-custom")
    env.make_alarm("ALARM", name=DEFAULT_ALARM)
    run_breaker()
    assert env.sizes() == (0, 1, 1)
    assert env.protection() == {i: True for i in ids}


def test_idle_alarm_in_alarm_under_a_custom_name_stops_the_group(env, monkeypatch):
    monkeypatch.setenv("IDLE_ALARM", "neurolens-worker-idle-custom")
    env.make_group(0, 1, 1)
    env.make_alarm("ALARM", name="neurolens-worker-idle-custom")
    env.make_alarm("OK", name=DEFAULT_ALARM)
    run_breaker()
    assert env.sizes() == (0, 0, 0)
    assert env.instance_ids() == []


def test_default_alarm_name_is_neurolens_worker_idle(env):
    """IDLE_ALARM unset (the env fixture removes it): the alarm read is neurolens-worker-idle."""
    ids = env.make_group(0, 1, 1)
    env.make_alarm("OK", name=DEFAULT_ALARM)
    run_breaker()
    assert env.sizes() == (0, 1, 1)
    assert env.protection() == {i: True for i in ids}
