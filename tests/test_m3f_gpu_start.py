"""Dispatcher-started GPU and the GPU name in the boot record (docs/M3f_spec.md §6a).

The dispatcher starts the GPU after a successful async invoke, so the cold start does not wait
for the scale-out alarm. These tests use small hand-written SageMaker control-plane fakes and the
same PostgreSQL/S3 fixtures as the M3e dispatcher tests; the GPU is never really woken.
"""

import json
import logging
import uuid
from datetime import UTC, datetime

import pytest
from botocore.exceptions import ClientError

ENDPOINT = "neurolens-gpu"
MAX_UPLOAD_BYTES = 300_000_000
MAX_S = 120


class RecordingControl:
    """A SageMaker control-plane client: one variant, or a describe/update failure."""

    def __init__(
        self,
        desired=0,
        *,
        variant="main",
        variants=None,
        describe_error=None,
        update_error=None,
        events=None,
    ):
        self.desired = desired
        self.variant = variant
        self.variants = variants
        self.describe_error = describe_error
        self.update_error = update_error
        self.events = events
        self.describes = []
        self.updates = []

    def describe_endpoint(self, **kwargs):
        self.describes.append(kwargs)
        if self.events is not None:
            self.events.append("describe")
        if self.describe_error is not None:
            raise self.describe_error
        if self.variants is not None:
            return {"ProductionVariants": self.variants}
        return {
            "ProductionVariants": [
                {"VariantName": self.variant, "DesiredInstanceCount": self.desired}
            ]
        }

    def update_endpoint_weights_and_capacities(self, **kwargs):
        self.updates.append(kwargs)
        if self.events is not None:
            self.events.append("update")
        if self.update_error is not None:
            raise self.update_error
        return {}


class RecordingSageMaker:
    """The runtime client: InvokeEndpointAsync, recorded."""

    def __init__(self, events=None):
        self.events = events
        self.calls = []

    def invoke_endpoint_async(self, **kwargs):
        self.calls.append(kwargs)
        if self.events is not None:
            self.events.append("invoke")
        return {"InferenceId": kwargs["InferenceId"]}


class NotPausedAppScaling:
    def describe_scalable_targets(self, **kwargs):
        return {"ScalableTargets": [{"MinCapacity": 0, "MaxCapacity": 1}]}


def client_error(code, operation="UpdateEndpointWeightsAndCapacities"):
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


def start(control, endpoint=ENDPOINT):
    from neurolens.lambdas import dispatcher

    return dispatcher.start_gpu_if_off(control, endpoint)


def errors(caplog):
    return [record for record in caplog.records if record.levelno >= logging.ERROR]


# ---------------------------------------------------------------- start_gpu_if_off


def test_zero_desired_instances_starts_the_gpu():
    control = RecordingControl(desired=0, variant="main")

    assert start(control) == "started"

    assert control.describes == [{"EndpointName": ENDPOINT}]
    assert control.updates == [
        {
            "EndpointName": ENDPOINT,
            "DesiredWeightsAndCapacities": [{"VariantName": "main", "DesiredInstanceCount": 1}],
        }
    ]


def test_one_desired_instance_needs_no_start():
    control = RecordingControl(desired=1, variant="main")

    assert start(control) == "running"

    assert control.describes == [{"EndpointName": ENDPOINT}]
    assert control.updates == []


def test_an_update_already_running_is_not_an_error(caplog):
    caplog.set_level(logging.DEBUG)
    control = RecordingControl(desired=0, update_error=client_error("ValidationException"))

    assert start(control) == "starting"

    assert control.updates != []
    assert errors(caplog) == []


@pytest.mark.parametrize(
    "make_control",
    [
        lambda: RecordingControl(describe_error=RuntimeError("sagemaker is down")),
        lambda: RecordingControl(variants=[]),
        lambda: RecordingControl(
            variants=[
                {"VariantName": "main", "DesiredInstanceCount": 1},
                {"VariantName": "other", "DesiredInstanceCount": 0},
            ]
        ),
    ],
    ids=["describe-fails", "no-variants", "two-variants"],
)
def test_an_unusable_endpoint_description_is_logged(make_control, caplog):
    caplog.set_level(logging.DEBUG)
    control = make_control()

    assert start(control) == "error"

    assert control.updates == []
    assert errors(caplog), "an operator must see why the GPU did not start"


def test_update_failure_other_than_validation_is_logged(caplog):
    caplog.set_level(logging.DEBUG)
    control = RecordingControl(desired=0, update_error=client_error("AccessDenied"))

    assert start(control) == "error"

    assert control.updates != []
    assert errors(caplog)


# ---------------------------------------------------------------- the boot record


def test_boot_record_carries_the_gpu_name():
    start_time = datetime(2026, 10, 14, 3, 0, 41, tzinfo=UTC)
    record = worker_boot_record(
        start_time,
        datetime(2026, 10, 14, 3, 2, 9, tzinfo=UTC),
        datetime(2026, 10, 14, 3, 3, 55, tzinfo=UTC),
        datetime(2026, 10, 14, 3, 5, 58, tzinfo=UTC),
        "NVIDIA L4",
    )

    assert record["gpu_name"] == "NVIDIA L4"


def test_boot_record_keeps_a_missing_gpu_name_as_none():
    start_time = datetime(2026, 10, 14, 3, 0, 41, tzinfo=UTC)
    record = worker_boot_record(start_time, None, None, start_time, None)

    assert record["gpu_name"] is None


def worker_boot_record(*args):
    from neurolens import worker

    return worker.boot_record(*args)


# ---------------------------------------------------------------- the SQS handler


def probe(monkeypatch):
    """Replace dispatcher.probe_duration with a short, offline video probe."""
    from neurolens.lambdas import dispatcher

    def install(seconds=3.0, error=None):
        def fake(url, timeout_s=30):
            if error is not None:
                raise error
            return seconds

        monkeypatch.setattr(dispatcher, "probe_duration", fake)

    return install


def upload(aws, key, body=b"an uploaded video"):
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=body)


def sqs_event(key, bucket):
    body = json.dumps(
        {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key, "size": 1}}}]}
    )
    return {
        "Records": [
            {
                "messageId": str(uuid.uuid4()),
                "receiptHandle": "receipt",
                "body": body,
                "eventSource": "aws:sqs",
                "eventSourceARN": "arn:aws:sqs:us-east-1:000000000000:neurolens-jobs",
            }
        ]
    }


def run_handler(aws, db, monkeypatch, key, runtime, control):
    import neurolens.lambdas as lambdas

    monkeypatch.setattr(lambdas, "database", lambda: db)
    monkeypatch.setattr(lambdas, "s3_client", lambda: aws.s3)
    monkeypatch.setattr(lambdas, "sagemaker_runtime_client", lambda: runtime)
    monkeypatch.setattr(lambdas, "sagemaker_client", lambda: control)
    monkeypatch.setattr(lambdas, "appscaling_client", lambda: NotPausedAppScaling(), raising=False)
    monkeypatch.setenv("NEUROLENS_S3_BUCKET", aws.bucket)
    monkeypatch.setenv("NEUROLENS_GPU_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("NEUROLENS_MAX_UPLOAD_BYTES", str(MAX_UPLOAD_BYTES))
    monkeypatch.setenv("NEUROLENS_MAX_DURATION_SECONDS", str(MAX_S))

    from neurolens.lambdas import dispatcher

    return dispatcher.handler(sqs_event(key, aws.bucket), None)


def test_the_handler_starts_the_gpu_after_dispatching(aws, db, new_job, monkeypatch):
    job_id, key = new_job()
    upload(aws, key)
    probe(monkeypatch)(seconds=3.0)
    events = []
    runtime = RecordingSageMaker(events=events)
    control = RecordingControl(desired=0, variant="main", events=events)

    assert run_handler(aws, db, monkeypatch, key, runtime, control) == ["invoked"]

    assert runtime.calls[0]["InferenceId"] == job_id
    assert control.describes == [{"EndpointName": ENDPOINT}]
    assert control.updates == [
        {
            "EndpointName": ENDPOINT,
            "DesiredWeightsAndCapacities": [{"VariantName": "main", "DesiredInstanceCount": 1}],
        }
    ]
    assert events == ["invoke", "describe", "update"]


def test_a_refunded_job_never_touches_the_gpu(aws, db, new_job, monkeypatch):
    from neurolens.lambdas import dispatcher
    from neurolens.media import UnreadableVideo

    job_id, key = new_job()
    upload(aws, key)
    probe(monkeypatch)(error=UnreadableVideo("not a readable video"))
    runtime = RecordingSageMaker()
    control = RecordingControl(desired=0)
    starts = []
    monkeypatch.setattr(
        dispatcher, "start_gpu_if_off", lambda *args, **kwargs: starts.append(args) or "started"
    )

    assert run_handler(aws, db, monkeypatch, key, runtime, control) == ["refunded"]

    assert runtime.calls == []
    assert starts == []
    assert control.describes == [] and control.updates == []


def test_a_failed_gpu_start_leaves_the_job_dispatched(aws, db, new_job, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    job_id, key = new_job()
    upload(aws, key)
    probe(monkeypatch)(seconds=3.0)
    runtime = RecordingSageMaker()
    control = RecordingControl(describe_error=RuntimeError("sagemaker is down"))

    assert run_handler(aws, db, monkeypatch, key, runtime, control) == ["invoked"]

    assert [call["InferenceId"] for call in runtime.calls] == [job_id]
    assert control.updates == []
    assert errors(caplog), "the failed start must be visible in the log"
