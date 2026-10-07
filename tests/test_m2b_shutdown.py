"""M2b shutdown handling and run(). Written from docs/M2b_spec.md §1a (ShutdownRequested,
ShutdownSignal, process_message, run), §6 and §11, rewritten for docs/M3a_spec.md §5: the new
signatures (db, heartbeat(on_beat)); process_message no longer writes any status (M2b's
"interrupted" final-attempt status moves into handle_record's release_for_retry, checked here on
the real pipeline); max_receives is kept only for the log line; run() builds the database with
neurolens.db.from_config and refuses a heartbeat interval over 50 s.

Every test that installs a ShutdownSignal uninstalls it (the `shutdown` fixture), and
tests/conftest.py restores pytest's SIGTERM handler after every test as a second guard. Only one
test sends a real signal (os.kill); the others call the handler directly.
"""

import json
import os
import signal
import time
import uuid

import pytest
from botocore.exceptions import ClientError
from conftest import USER_ID
from neurolens import inference, settings, worker
from neurolens.worker import Outcome, process_message

MODULES = ("neurolens.inference", "neurolens.worker")
INTERRUPTED = "The job was interrupted. Please upload it again."
NO_DB = object()  # process_message only hands the database on to (a scripted) handle_record
RUN_DB_CFG = {"backend": "postgres", "dsn": "postgresql://unused.invalid/neurolens"}


@pytest.fixture(autouse=True)
def fake_on(monkeypatch):
    monkeypatch.setenv("FAKE_INFERENCE", "1")
    monkeypatch.delenv("NEUROLENS_DEPLOYED", raising=False)  # no boot records from run()


@pytest.fixture(autouse=True)
def no_gpu(patch_everywhere):
    patch_everywhere("gpu_info", lambda: None, *MODULES)


@pytest.fixture
def shutdown():
    sig = worker.ShutdownSignal()
    sig.install()
    try:
        yield sig
    finally:
        sig.uninstall()


def s3_event(*pairs):
    return json.dumps(
        {
            "Records": [
                {"s3": {"bucket": {"name": b}, "object": {"key": k, "size": 1}}} for b, k in pairs
            ]
        }
    )


def new_upload():
    job_id = str(uuid.uuid4())
    return job_id, f"uploads/{USER_ID}/{job_id}.mp4"


@pytest.fixture
def received(aws):
    """Send and receive like the worker: hidden for 120 s, receive count "1"."""

    def put(body):
        aws.sqs.send_message(QueueUrl=aws.queue_url, MessageBody=body)
        resp = aws.sqs.receive_message(
            QueueUrl=aws.queue_url,
            MaxNumberOfMessages=1,
            VisibilityTimeout=120,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
        return resp["Messages"][0]

    return put


def visible(aws):
    resp = aws.sqs.receive_message(
        QueueUrl=aws.queue_url, MaxNumberOfMessages=10, VisibilityTimeout=0
    )
    return resp.get("Messages", [])


def queue_counts(aws):
    attrs = aws.sqs.get_queue_attributes(
        QueueUrl=aws.queue_url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    return int(attrs["ApproximateNumberOfMessages"]) + int(
        attrs["ApproximateNumberOfMessagesNotVisible"]
    )


@pytest.fixture
def fake_record(monkeypatch):
    """Replace handle_record (the §1a signature) with fn(heartbeat) for every key."""

    def install(fn):
        seen = []

        def fake(bucket, key, *, s3, db, cfg, roi_masks, heartbeat):
            seen.append(key)
            return fn(heartbeat)

        monkeypatch.setattr(worker, "handle_record", fake)
        return seen

    return install


# ---------------------------------------------------------------- ShutdownRequested


def test_shutdown_requested_is_a_base_exception_not_an_exception():
    assert issubclass(worker.ShutdownRequested, BaseException)
    assert not issubclass(worker.ShutdownRequested, Exception)


def test_shutdown_requested_is_not_caught_by_except_exception():
    swallowed = []
    with pytest.raises(worker.ShutdownRequested):
        try:
            raise worker.ShutdownRequested()
        except Exception:  # what a library's broad handler looks like
            swallowed.append(True)
    assert swallowed == []


# ---------------------------------------------------------------- ShutdownSignal


def test_install_registers_handle_for_sigterm_and_uninstall_restores_the_previous_handler():
    def previous(signum, frame):
        pass

    signal.signal(signal.SIGTERM, previous)
    sig = worker.ShutdownSignal()
    sig.install()
    try:
        assert signal.getsignal(signal.SIGTERM) == sig.handle
    finally:
        sig.uninstall()
    assert signal.getsignal(signal.SIGTERM) is previous


def test_not_requested_before_any_signal(shutdown):
    assert shutdown.requested() is False


def test_outside_job_handle_only_records_the_request(shutdown):
    shutdown.handle(signal.SIGTERM, None)  # must not raise
    assert shutdown.requested() is True


def test_after_a_job_block_has_ended_handle_only_records(shutdown):
    with shutdown.job():
        pass
    shutdown.handle(signal.SIGTERM, None)  # must not raise: no record work in progress
    assert shutdown.requested() is True


def test_handle_inside_a_pipeline_stage_raises_at_once(shutdown):
    reached_after_signal = []

    def video_encoding_stage():
        shutdown.handle(signal.SIGTERM, None)  # the signal arrives mid-stage
        reached_after_signal.append(True)  # must never run

    with pytest.raises(worker.ShutdownRequested):
        with shutdown.job():
            video_encoding_stage()
    assert reached_after_signal == []
    assert shutdown.requested() is True


def test_a_second_signal_does_not_raise_again(shutdown):
    with shutdown.job():
        with pytest.raises(worker.ShutdownRequested):
            shutdown.handle(signal.SIGTERM, None)
        shutdown.handle(signal.SIGTERM, None)  # same block: no second raise
    with shutdown.job():
        shutdown.handle(signal.SIGTERM, None)  # a later block: still no second raise
    assert shutdown.requested() is True


def test_a_real_sigterm_inside_job_raises_at_once(shutdown):
    """The one real-signal test: os.kill on this process while a stage is sleeping."""
    # Guard: never send SIGTERM unless the handler is in place (it would end the test run).
    assert signal.getsignal(signal.SIGTERM) == shutdown.handle
    start = time.monotonic()
    with pytest.raises(worker.ShutdownRequested):
        with shutdown.job():
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(5)  # a long stage; the signal must cut it short
    assert time.monotonic() - start < 2
    assert shutdown.requested() is True


# ---------------------------------------------------------------- process_message on shutdown


def signal_mid_record(shutdown, then=None):
    """A record that is running (inside its heartbeat) when SIGTERM arrives. `then` turns the
    ShutdownRequested into another exception, as when a killed subprocess fails first."""

    def run(heartbeat):
        with heartbeat(None):
            if then is None:
                shutdown.handle(signal.SIGTERM, None)
                raise AssertionError("handle should have raised ShutdownRequested")
            try:
                shutdown.handle(signal.SIGTERM, None)
            except worker.ShutdownRequested:
                pass
            raise then

    return run


def call_process(aws, message, cfg, masks, shutdown, max_receives, sqs=None, db=NO_DB):
    return process_message(
        message,
        s3=aws.s3,
        sqs=sqs or aws.sqs,
        db=db,
        cfg=cfg,
        roi_masks=masks,
        shutdown=shutdown,
        max_receives=max_receives,
    )


@pytest.mark.parametrize("max_receives", [2, 1], ids=["non_final", "final"])
@pytest.mark.parametrize(
    "then", [None, RuntimeError("ffmpeg was killed")], ids=["shutdown_requested", "other_error"]
)
def test_shutdown_releases_the_message_and_reraises_on_any_attempt(
    aws, make_cfg, roi_masks_small, received, fake_record, shutdown, then, max_receives
):
    """M3a §5: on a shutdown process_message releases the message and re-raises; it writes no
    status on any attempt (handle_record has already released the job, and the dead-letter
    handler settles one that never comes back)."""
    _, key = new_upload()
    fake_record(signal_mid_record(shutdown, then))
    message = received(s3_event((aws.bucket, key)))

    with pytest.raises(worker.ShutdownRequested):
        call_process(aws, message, make_cfg(), roi_masks_small, shutdown, max_receives=max_receives)

    assert len(visible(aws)) == 1  # released, not deleted


def test_a_message_received_after_a_request_is_released_untouched(
    aws, make_cfg, roi_masks_small, received, fake_record, shutdown
):
    _, key = new_upload()
    seen = fake_record(lambda heartbeat: Outcome.DONE)
    message = received(s3_event((aws.bucket, key)))
    shutdown.handle(signal.SIGTERM, None)  # between messages: only recorded

    with pytest.raises(worker.ShutdownRequested):
        call_process(aws, message, make_cfg(), roi_masks_small, shutdown, max_receives=1)

    assert seen == []  # no record work at all
    assert len(visible(aws)) == 1


def test_a_signal_during_the_message_delete_does_not_interrupt_it(
    aws, make_cfg, roi_masks_small, received, fake_record, shutdown
):
    """Only the record work is inside job(): the delete after it cannot be cut short."""
    _, key = new_upload()
    fake_record(lambda heartbeat: Outcome.DONE)
    raised_inside_delete = []

    class SignalDuringDelete:
        def __init__(self, inner):
            self._inner = inner

        def delete_message(self, **kwargs):
            try:
                shutdown.handle(signal.SIGTERM, None)
            except worker.ShutdownRequested:
                raised_inside_delete.append(True)
                raise
            return self._inner.delete_message(**kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    message = received(s3_event((aws.bucket, key)))
    try:
        call_process(
            aws,
            message,
            make_cfg(),
            roi_masks_small,
            shutdown,
            max_receives=2,
            sqs=SignalDuringDelete(aws.sqs),
        )
    except worker.ShutdownRequested:
        pass  # whether it re-raises afterwards is not fixed by the spec
    assert raised_inside_delete == []
    assert queue_counts(aws) == 0  # deleted
    assert shutdown.requested() is True


def test_real_pipeline_interrupted_mid_inference_is_released_with_its_last_stage(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, received, shutdown, patch_everywhere
):
    """M3a §5: handle_record hands the job back with release_for_retry and M2b's "interrupted"
    message (so a job dead-lettered after a shutdown still shows a reason); money stays
    reserved; process_message releases the message and re-raises."""
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)

    def predict(*a, **kw):
        shutdown.handle(signal.SIGTERM, None)
        raise AssertionError("handle should have raised ShutdownRequested")

    patch_everywhere("predict", predict, *MODULES)
    message = received(s3_event((aws.bucket, key)))

    with pytest.raises(worker.ShutdownRequested):
        call_process(aws, message, make_cfg(), roi_masks_small, shutdown, max_receives=2, db=db)

    job = pg.job(job_id)
    assert job["status"] == "queued"
    assert job["error_message"] == INTERRUPTED
    assert job["stages"][-1]["stage"] == "inference_full"
    assert pg.balance() == (410, 90)
    assert len(visible(aws)) == 1
    with pytest.raises(ClientError):
        aws.s3.head_object(Bucket=aws.bucket, Key=f"results/{job_id}.json")


# ---------------------------------------------------------------- run()


class Stop(BaseException):
    """Safety net: ends a run() that ignored the shutdown request (nothing may swallow it)."""


def sigterm_handler_now():
    """Call whatever SIGTERM handler run() installed, as the signal would (no real signal)."""
    handler = signal.getsignal(signal.SIGTERM)
    assert callable(handler), "run() did not install a SIGTERM handler"
    handler(signal.SIGTERM, None)


class RunSqs:
    """A fake SQS for run(). `deliver` maps poll numbers (1-based) to (key, receive count);
    on poll `shutdown_on` the installed SIGTERM handler is called between messages (outside any
    job); Stop is raised after `limit` polls."""

    def __init__(self, bucket, *, policy, deliver=None, shutdown_on=None, limit=200):
        self.bucket = bucket
        self.policy = policy
        self.deliver = deliver or {}
        self.shutdown_on = shutdown_on
        self.limit = limit
        self.receives = []
        self.visibility = []
        self.deleted = 0

    def get_queue_attributes(self, **kwargs):
        attrs = {}
        if self.policy is not None:
            attrs["RedrivePolicy"] = json.dumps(self.policy)
        return {"Attributes": attrs}

    def receive_message(self, **kwargs):
        time.sleep(0.01)
        self.receives.append(kwargs)
        poll = len(self.receives)
        if poll > self.limit:
            raise Stop
        if poll == self.shutdown_on:
            sigterm_handler_now()
            return {}
        if poll in self.deliver:
            key, count = self.deliver[poll]
            return {
                "Messages": [
                    {
                        "MessageId": str(poll),
                        "ReceiptHandle": f"r{poll}",
                        "Body": s3_event((self.bucket, key)),
                        "Attributes": {"ApproximateReceiveCount": count},
                    }
                ]
            }
        return {}

    def change_message_visibility(self, **kwargs):
        self.visibility.append(kwargs["VisibilityTimeout"])
        return {}

    def delete_message(self, **kwargs):
        self.deleted += 1
        return {}


@pytest.fixture
def run_env(aws, make_cfg, monkeypatch, roi_masks_small, patch_everywhere):
    """Wire worker.run() to a config, a no-op model, moto S3, a fake SQS and a stand-in database
    (neurolens.db.from_config returns NO_DB; the scripted records never use it)."""
    import boto3

    def setup(sqs, **cfg_overrides):
        cfg = make_cfg(**{"db": dict(RUN_DB_CFG), **cfg_overrides})
        monkeypatch.setattr(settings, "load_settings", lambda *a, **kw: cfg)
        built.clear()
        monkeypatch.setattr(inference, "load_model", lambda *a, **kw: None)
        monkeypatch.setattr(inference, "roi_masks", lambda *a, **kw: roi_masks_small)

        def fake_client(service, **kwargs):
            return sqs if service == "sqs" else aws.s3

        monkeypatch.setattr(boto3, "client", fake_client)
        return cfg

    built = []

    def from_config(cfg):
        built.append(cfg)
        return NO_DB

    patch_everywhere("from_config", from_config, "neurolens.db", "neurolens.worker")
    setup.built = built
    return setup


def test_run_has_no_idle_exit_and_keeps_polling_until_a_shutdown_request(run_env, aws):
    # 30 empty polls of 0.01 s: far past the old idle limit set here (0.0001 min = 6 ms).
    sqs = RunSqs(aws.bucket, policy={"maxReceiveCount": 2}, shutdown_on=30)
    run_env(sqs, worker={"heartbeat_seconds": 50, "max_job_minutes": 75, "idle_exit_minutes": 1e-4})
    assert worker.run() is None  # returns normally once the request is seen
    assert len(sqs.receives) == 30  # stopped right after the poll in which the request came


def test_run_receives_with_the_approximate_receive_count(run_env, aws):
    sqs = RunSqs(aws.bucket, policy={"maxReceiveCount": 2}, shutdown_on=1)
    run_env(sqs)
    worker.run()
    names = sqs.receives[0].get("MessageSystemAttributeNames", [])
    assert "ApproximateReceiveCount" in names or "All" in names


@pytest.mark.parametrize("policy_count", ["10", 2], ids=["string", "integer"])
def test_run_reads_max_receives_from_the_redrive_policy_as_an_integer(
    run_env, aws, monkeypatch, policy_count
):
    """M3a §5: max_receives is kept only for the log line ("attempt 1 of 2"); run() still reads
    it once from the queue's RedrivePolicy, as an integer, and passes it on."""
    _, key = new_upload()
    sqs = RunSqs(
        aws.bucket,
        policy={
            "deadLetterTargetArn": "arn:aws:sqs:us-east-1:1:dlq",
            "maxReceiveCount": policy_count,
        },
        deliver={1: (key, "1")},
        shutdown_on=2,
    )
    run_env(sqs)
    passed = []

    def fake_process_message(message, **kwargs):
        passed.append(kwargs)

    monkeypatch.setattr(worker, "process_message", fake_process_message)
    worker.run()
    assert [kw["max_receives"] for kw in passed] == [int(policy_count)]
    assert type(passed[0]["max_receives"]) is int


def test_run_builds_the_database_from_config_and_hands_it_to_each_record(run_env, aws, monkeypatch):
    _, key = new_upload()
    sqs = RunSqs(aws.bucket, policy={"maxReceiveCount": 2}, deliver={1: (key, "1")}, shutdown_on=2)
    cfg = run_env(sqs)
    seen = []

    def fake(bucket, key, *, s3, db, cfg, roi_masks, heartbeat):
        seen.append(db)
        return Outcome.DONE

    monkeypatch.setattr(worker, "handle_record", fake)
    worker.run()
    assert run_env.built == [cfg]
    assert seen == [NO_DB]


@pytest.mark.parametrize("seconds", [51, 120])
def test_run_refuses_a_heartbeat_interval_over_50_seconds(run_env, aws, caplog, seconds):
    """M3a §5: a redelivery comes no sooner than 120 s after the last heartbeat, and a claim is
    stale after 90 s, so the heartbeat must refresh it every 50 s or less."""
    sqs = RunSqs(aws.bucket, policy={"maxReceiveCount": 2}, shutdown_on=1, limit=5)
    run_env(sqs, worker={"heartbeat_seconds": seconds, "max_job_minutes": 75})
    error = None
    with caplog.at_level("DEBUG"):
        try:
            worker.run()
        except Stop:
            pytest.fail("run() started polling with a heartbeat interval over 50 s")
        except Exception as err:  # raising or logging are both a refusal
            error = err
    assert sqs.receives == []
    said = str(error or "") + " ".join(r.getMessage() for r in caplog.records)
    assert "heartbeat_seconds" in said


def test_run_accepts_a_heartbeat_interval_of_50_seconds(run_env, aws):
    sqs = RunSqs(aws.bucket, policy={"maxReceiveCount": 2}, shutdown_on=1)
    run_env(sqs, worker={"heartbeat_seconds": 50, "max_job_minutes": 75})
    worker.run()
    assert len(sqs.receives) == 1


def test_run_refuses_to_start_when_the_queue_has_no_redrive_policy(run_env, aws, caplog):
    sqs = RunSqs(aws.bucket, policy=None, shutdown_on=1, limit=5)
    run_env(sqs)
    error = None
    with caplog.at_level("DEBUG"):
        try:
            worker.run()
        except Stop:
            pytest.fail("run() started polling without a RedrivePolicy")
        except Exception as err:  # raising or logging are both a refusal
            error = err
    assert sqs.receives == []
    said = str(error or "") + " ".join(r.getMessage() for r in caplog.records)
    assert "RedrivePolicy" in said or "maxReceiveCount" in said


def test_run_stops_and_returns_after_a_shutdown_inside_a_job(run_env, aws, monkeypatch):
    _, key = new_upload()
    sqs = RunSqs(aws.bucket, policy={"maxReceiveCount": 2}, deliver={1: (key, "1")})

    def fake(bucket, key, *, s3, db, cfg, roi_masks, heartbeat):
        with heartbeat(None):
            sigterm_handler_now()  # inside the record's work: raises ShutdownRequested
        raise AssertionError("the handler should have raised")

    run_env(sqs)
    monkeypatch.setattr(worker, "handle_record", fake)
    assert worker.run() is None  # returns normally (exit code 0): systemd does not restart it
    assert len(sqs.receives) == 1  # no more messages after the shutdown
    assert sqs.visibility[-1] == 0  # the job was released
    assert sqs.deleted == 0


def test_run_stops_after_a_shutdown_request_between_messages(run_env, aws, monkeypatch):
    _, key = new_upload()
    sqs = RunSqs(
        aws.bucket,
        policy={"maxReceiveCount": 2},
        deliver={1: (key, "1"), 3: (key, "1")},
        shutdown_on=2,
    )
    run_env(sqs)
    monkeypatch.setattr(worker, "handle_record", lambda bucket, key, **kw: Outcome.DONE)
    assert worker.run() is None
    assert len(sqs.receives) == 2  # poll 3's message is never received
    assert sqs.deleted == 1
