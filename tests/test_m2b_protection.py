"""M2b scale-in protection on the worker. Written first from docs/M2b_spec.md §1a
(`ScaleInProtection`, `poll_once(..., protection=None)`, `validate_config` with `aws.worker_group`)
and §11.

Auto Scaling runs on moto, inside the same mock as the `aws` fixture's S3 and SQS. moto models
scale-in protection like AWS does (`set_instance_protection`, `ProtectedFromScaleIn` in
`describe_auto_scaling_instances`), so "protected" is read back from the fake AWS, not from call
records. Only the failure tests use a small fake client, because moto cannot make the call fail.

conftest's `isolate_env_settings` clears a fixed list of variables that does not include
NEUROLENS_WORKER_GROUP or NEUROLENS_DEPLOYED, so this file clears both itself.
"""

import json
import logging
import signal
import uuid

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from neurolens import settings, worker
from neurolens.worker import Outcome

GROUP = "neurolens-workers"
NO_DB = object()  # M3a §5: poll_once hands the database on; the scripted records never use it
MAX_RECEIVES = 2  # the job queue's maxReceiveCount (M2a §4h); every message here is receive 1


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("NEUROLENS_DEPLOYED", raising=False)
    monkeypatch.delenv("NEUROLENS_WORKER_GROUP", raising=False)
    monkeypatch.setenv("FAKE_INFERENCE", "1")


@pytest.fixture
def group(aws):
    """One running worker in a moto Auto Scaling group, unprotected; same mock as `aws`.

    Returns (autoscaling client, instance id)."""
    ec2 = boto3.client("ec2", region_name="us-east-1")
    autoscaling = boto3.client("autoscaling", region_name="us-east-1")
    ami = ec2.describe_images()["Images"][0]["ImageId"]
    ec2.create_launch_template(
        LaunchTemplateName="neurolens-worker",
        LaunchTemplateData={"ImageId": ami, "InstanceType": "t3.large"},
    )
    autoscaling.create_auto_scaling_group(
        AutoScalingGroupName=GROUP,
        LaunchTemplate={"LaunchTemplateName": "neurolens-worker", "Version": "$Latest"},
        MinSize=0,
        MaxSize=1,
        DesiredCapacity=1,
        AvailabilityZones=["us-east-1a"],
    )
    instances = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[GROUP])[
        "AutoScalingGroups"
    ][0]["Instances"]
    instance_id = instances[0]["InstanceId"]
    assert not protected(autoscaling, instance_id)  # starting state
    return autoscaling, instance_id


def protected(autoscaling, instance_id):
    found = autoscaling.describe_auto_scaling_instances(InstanceIds=[instance_id])[
        "AutoScalingInstances"
    ]
    return found[0]["ProtectedFromScaleIn"]


class RecordingAutoScaling:
    """Delegates to a real (moto) client and records the names of the calls made."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = []

    def __getattr__(self, name):
        attr = getattr(self._inner, name)

        def wrapper(*args, **kwargs):
            self.calls.append(name)
            return attr(*args, **kwargs)

        return wrapper


class FailingAutoScaling:
    """moto cannot make set_instance_protection fail, so this fake raises `error` on every call."""

    def __init__(self, error):
        self.error = error
        self.calls = 0

    def set_instance_protection(self, **kwargs):
        self.calls += 1
        raise self.error


FAILURES = [
    ClientError(
        {"Error": {"Code": "ValidationError", "Message": "not in group"}}, "SetInstanceProtection"
    ),
    EndpointConnectionError(endpoint_url="https://autoscaling.us-east-1.amazonaws.com"),
]


# ---------------------------------------------------------------- ScaleInProtection.hold()


def test_hold_protects_inside_the_block_and_unprotects_after(group):
    autoscaling, instance_id = group
    protection = worker.ScaleInProtection(autoscaling, GROUP, instance_id)
    with protection.hold():
        assert protected(autoscaling, instance_id)
    assert not protected(autoscaling, instance_id)


def test_hold_unprotects_and_propagates_an_ordinary_exception(group):
    autoscaling, instance_id = group
    protection = worker.ScaleInProtection(autoscaling, GROUP, instance_id)
    with pytest.raises(ValueError, match="job failed"):
        with protection.hold():
            assert protected(autoscaling, instance_id)
            raise ValueError("job failed")
    assert not protected(autoscaling, instance_id)


def test_hold_unprotects_and_propagates_shutdown_requested(group):
    autoscaling, instance_id = group
    protection = worker.ScaleInProtection(autoscaling, GROUP, instance_id)
    with pytest.raises(worker.ShutdownRequested):
        with protection.hold():
            assert protected(autoscaling, instance_id)
            raise worker.ShutdownRequested()
    assert not protected(autoscaling, instance_id)


@pytest.mark.parametrize("error", FAILURES, ids=["ClientError", "EndpointConnectionError"])
def test_hold_with_a_failing_call_logs_does_not_raise_and_still_runs_the_block(error, caplog):
    client = FailingAutoScaling(error)
    protection = worker.ScaleInProtection(client, GROUP, "i-0123456789abcdef0")
    ran = []
    with caplog.at_level(logging.WARNING):
        with protection.hold():
            ran.append(True)
    assert ran == [True]
    assert client.calls >= 1  # the call was attempted, so the failure path was exercised
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


@pytest.mark.parametrize("error", FAILURES, ids=["ClientError", "EndpointConnectionError"])
def test_hold_with_a_failing_call_lets_the_blocks_own_exception_through(error):
    """Natural reading of §1a: a failed protection call is never raised, so it cannot replace
    the job's own exception either (the caller must see the real error or ShutdownRequested)."""
    protection = worker.ScaleInProtection(FailingAutoScaling(error), GROUP, "i-0123456789abcdef0")
    with pytest.raises(worker.ShutdownRequested):
        with protection.hold():
            raise worker.ShutdownRequested()


# ---------------------------------------------------------------- poll_once with a protection


class ShortPollSqs:
    """Delegates to moto but never waits 20 s on an empty queue."""

    def __init__(self, inner):
        self._inner = inner

    def receive_message(self, **kwargs):
        kwargs.update(WaitTimeSeconds=0)
        return self._inner.receive_message(**kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def s3_event(bucket, key):
    return json.dumps(
        {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key, "size": 1}}}]}
    )


def send_job(aws):
    key = f"uploads/test-user/{uuid.uuid4()}.mp4"
    aws.sqs.send_message(QueueUrl=aws.queue_url, MessageBody=s3_event(aws.bucket, key))
    return key


def fake_record(monkeypatch, body):
    """Replace handle_record (the M3a §5 signature) with body(); records each key it is given."""
    seen = []

    def fake(bucket, key, *, s3, db, cfg, roi_masks, heartbeat):
        seen.append(key)
        return body()

    monkeypatch.setattr(worker, "handle_record", fake)
    return seen


def poll(aws, make_cfg, roi_masks, *, shutdown=None, protection=None):
    return worker.poll_once(
        s3=aws.s3,
        sqs=ShortPollSqs(aws.sqs),
        db=NO_DB,
        cfg=make_cfg(),
        roi_masks=roi_masks,
        shutdown=shutdown or worker.ShutdownSignal(),
        max_receives=MAX_RECEIVES,
        protection=protection,
    )


def test_poll_once_handles_a_message_while_protected_and_unprotects_after(
    aws, make_cfg, roi_masks_small, group, monkeypatch
):
    autoscaling, instance_id = group
    protection = worker.ScaleInProtection(autoscaling, GROUP, instance_id)
    key = send_job(aws)
    during = []

    def body():
        during.append(protected(autoscaling, instance_id))
        return Outcome.DONE

    seen = fake_record(monkeypatch, body)

    assert poll(aws, make_cfg, roi_masks_small, protection=protection) is True
    assert seen == [key]
    assert during == [True]
    assert not protected(autoscaling, instance_id)


def test_poll_once_unprotects_when_the_job_is_interrupted_by_a_shutdown(
    aws, make_cfg, roi_masks_small, group, monkeypatch
):
    autoscaling, instance_id = group
    protection = worker.ScaleInProtection(autoscaling, GROUP, instance_id)
    send_job(aws)
    shutdown = worker.ShutdownSignal()
    during = []

    def body():
        during.append(protected(autoscaling, instance_id))
        shutdown.handle(signal.SIGTERM, None)  # inside job(): raises ShutdownRequested at once
        raise AssertionError("handle() should have raised ShutdownRequested")

    fake_record(monkeypatch, body)
    shutdown.install()
    try:
        with pytest.raises(worker.ShutdownRequested):
            poll(aws, make_cfg, roi_masks_small, shutdown=shutdown, protection=protection)
    finally:
        shutdown.uninstall()

    assert during == [True]
    assert not protected(autoscaling, instance_id)


def test_poll_once_with_an_empty_queue_makes_no_protection_call(
    aws, make_cfg, roi_masks_small, group, monkeypatch
):
    autoscaling, instance_id = group
    recording = RecordingAutoScaling(autoscaling)
    protection = worker.ScaleInProtection(recording, GROUP, instance_id)
    seen = fake_record(monkeypatch, lambda: Outcome.DONE)

    assert poll(aws, make_cfg, roi_masks_small, protection=protection) is True
    assert seen == []
    assert "set_instance_protection" not in recording.calls
    assert not protected(autoscaling, instance_id)


def test_poll_once_without_a_protection_makes_no_auto_scaling_call(
    aws, make_cfg, roi_masks_small, monkeypatch
):
    """protection=None (laptop, tests): the job runs and no Auto Scaling client is even made."""
    made = []
    real_client = boto3.client

    def watching_client(service, *args, **kwargs):
        made.append(service)
        if service == "autoscaling":
            raise AssertionError("poll_once without a protection must not use Auto Scaling")
        return real_client(service, *args, **kwargs)

    monkeypatch.setattr(boto3, "client", watching_client)
    key = send_job(aws)
    seen = fake_record(monkeypatch, lambda: Outcome.DONE)

    assert poll(aws, make_cfg, roi_masks_small, protection=None) is True
    assert seen == [key]
    assert "autoscaling" not in made


def test_poll_once_protection_defaults_to_none(aws, make_cfg, roi_masks_small, monkeypatch):
    """§1a: `protection=None` is the default, so M1's call shape still works."""
    key = send_job(aws)
    seen = fake_record(monkeypatch, lambda: Outcome.DONE)
    ok = worker.poll_once(
        s3=aws.s3,
        sqs=ShortPollSqs(aws.sqs),
        db=NO_DB,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        shutdown=worker.ShutdownSignal(),
        max_receives=MAX_RECEIVES,
    )
    assert ok is True
    assert seen == [key]


# ---------------------------------------------------------------- validate_config and worker_group


def test_validate_config_off_aws_does_not_need_worker_group(make_cfg):
    cfg = make_cfg()
    assert "worker_group" not in cfg["aws"]
    worker.validate_config(cfg)  # does not raise


def test_validate_config_on_aws_requires_worker_group(make_cfg, monkeypatch):
    monkeypatch.setenv("NEUROLENS_DEPLOYED", "1")
    with pytest.raises(ValueError, match="worker_group|NEUROLENS_WORKER_GROUP"):
        worker.validate_config(make_cfg())


def test_validate_config_on_aws_passes_with_worker_group(make_cfg, monkeypatch):
    monkeypatch.setenv("NEUROLENS_DEPLOYED", "1")
    cfg = make_cfg()
    cfg["aws"]["worker_group"] = GROUP
    worker.validate_config(cfg)  # does not raise


def test_worker_group_comes_from_neurolens_worker_group(monkeypatch):
    """§1a: aws.worker_group comes from the environment variable NEUROLENS_WORKER_GROUP (env.conf
    on AWS). Read like the other NEUROLENS_* deployment values, through settings.apply_env (so
    load_settings, which is apply_env(load_config()), gets it too)."""
    monkeypatch.setenv("NEUROLENS_WORKER_GROUP", GROUP)
    out = settings.apply_env({"aws": {"region": "us-east-1"}})
    assert out["aws"]["worker_group"] == GROUP
    assert out["aws"]["region"] == "us-east-1"


def test_worker_group_absent_when_the_variable_is_unset_or_empty(monkeypatch):
    monkeypatch.setenv("NEUROLENS_WORKER_GROUP", "")
    assert "worker_group" not in settings.apply_env({"aws": {}})["aws"]
    monkeypatch.delenv("NEUROLENS_WORKER_GROUP")
    assert "worker_group" not in settings.apply_env({"aws": {}})["aws"]
