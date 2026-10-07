"""M2b Heartbeat and release. Written from docs/M2b_spec.md §1a and §6.

Timing tests use tiny intervals and generous tolerances. They never assert an exact number of
calls: only "at least N while the block runs" and "none after exit".
"""

import threading
import time

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from neurolens import worker

QUEUE = "https://sqs.us-east-1.amazonaws.com/123456789012/neurolens-test-queue"
RECEIPT = "receipt-handle-1"


class RecordingSqs:
    """Records every change_message_visibility call (start time, kwargs). Optionally slow or
    failing."""

    def __init__(self, fail_first=0, call_seconds=0.0, error=None):
        self.calls = []
        self.lock = threading.Lock()
        self.fail_first = fail_first
        self.call_seconds = call_seconds
        self.error = error or ClientError(
            {"Error": {"Code": "ServiceUnavailable", "Message": "test"}}, "ChangeMessageVisibility"
        )

    def change_message_visibility(self, **kwargs):
        with self.lock:
            self.calls.append((time.monotonic(), kwargs))
            n = len(self.calls)
        if self.call_seconds:
            time.sleep(self.call_seconds)
        if n <= self.fail_first:
            raise self.error
        return {}

    def count(self):
        with self.lock:
            return len(self.calls)

    def starts(self):
        with self.lock:
            return [t for t, _ in self.calls]


# ---------------------------------------------------------------- Heartbeat


def test_heartbeat_extends_visibility_to_120_repeatedly_while_the_block_runs():
    sqs = RecordingSqs()
    with worker.Heartbeat(sqs, QUEUE, RECEIPT, interval_seconds=0.05):
        time.sleep(0.6)
    assert sqs.count() >= 3
    for _, kwargs in sqs.calls:
        assert kwargs["VisibilityTimeout"] == 120
        assert kwargs["ReceiptHandle"] == RECEIPT
        assert kwargs["QueueUrl"] == QUEUE


def test_heartbeat_makes_no_call_after_exit():
    sqs = RecordingSqs()
    with worker.Heartbeat(sqs, QUEUE, RECEIPT, interval_seconds=0.05):
        time.sleep(0.3)
    after_exit = sqs.count()
    time.sleep(0.4)
    assert sqs.count() == after_exit


def test_heartbeat_makes_no_call_before_the_first_interval():
    sqs = RecordingSqs()
    with worker.Heartbeat(sqs, QUEUE, RECEIPT, interval_seconds=0.6):
        time.sleep(0.15)
        assert sqs.count() == 0
        time.sleep(1.0)
        assert sqs.count() >= 1  # it does beat once the interval has passed


def test_heartbeat_uses_the_given_visibility_seconds():
    sqs = RecordingSqs()
    with worker.Heartbeat(sqs, QUEUE, RECEIPT, interval_seconds=0.05, visibility_seconds=300):
        time.sleep(0.3)
    assert sqs.count() >= 1
    assert {kwargs["VisibilityTimeout"] for _, kwargs in sqs.calls} == {300}


@pytest.mark.parametrize(
    "error",
    [
        ClientError(
            {"Error": {"Code": "ServiceUnavailable", "Message": "t"}}, "ChangeMessageVisibility"
        ),
        EndpointConnectionError(endpoint_url="https://sqs.us-east-1.amazonaws.com"),
    ],
    ids=["client_error", "network_down"],
)
def test_a_failing_beat_is_logged_and_later_beats_continue(caplog, error):
    sqs = RecordingSqs(fail_first=2, error=error)
    with caplog.at_level("DEBUG"):
        with worker.Heartbeat(sqs, QUEUE, RECEIPT, interval_seconds=0.05):
            time.sleep(0.6)
    assert sqs.count() >= 4  # two failures, then it kept beating
    assert any(r.levelname in ("WARNING", "ERROR", "CRITICAL") for r in caplog.records)


def test_heartbeat_stops_extending_after_max_seconds_while_the_block_still_runs(caplog):
    sqs = RecordingSqs()
    with caplog.at_level("DEBUG"):
        with worker.Heartbeat(sqs, QUEUE, RECEIPT, interval_seconds=0.05, max_seconds=0.3):
            time.sleep(0.7)  # well past max_seconds
            at_07 = sqs.count()
            time.sleep(0.5)
            at_12 = sqs.count()
    assert at_07 >= 1  # it did beat before the limit
    assert at_12 == at_07  # and stopped beating while the block was still running
    assert all(t - sqs.starts()[0] < 0.6 for t in sqs.starts())
    assert any(r.levelname in ("ERROR", "CRITICAL") for r in caplog.records)


def test_no_call_starts_after_exit_has_begun():
    """Flag under a lock: a beat already running may finish, but none may start once exit has
    begun (otherwise a beat could hide a released message again)."""
    for _ in range(5):  # a few rounds, to give a race a chance to show
        sqs = RecordingSqs(call_seconds=0.03)
        with worker.Heartbeat(sqs, QUEUE, RECEIPT, interval_seconds=0.01):
            time.sleep(0.2)
            exit_begins = time.monotonic()
        assert sqs.count() >= 1
        assert all(t <= exit_begins for t in sqs.starts())
        later = sqs.count()
        time.sleep(0.1)
        assert sqs.count() == later


def test_exit_waits_for_a_running_beat_to_finish():
    """Exit waits for a visibility call already in progress (M3a §5: it takes the beat's lock;
    it no longer waits for the thread, see tests/test_m3a_heartbeat.py): once the block has
    exited, no visibility call is still in progress."""
    finished = []

    class SlowSqs(RecordingSqs):
        def change_message_visibility(self, **kwargs):
            super().change_message_visibility(**kwargs)
            finished.append(time.monotonic())
            return {}

    sqs = SlowSqs(call_seconds=0.15)
    with worker.Heartbeat(sqs, QUEUE, RECEIPT, interval_seconds=0.05):
        time.sleep(0.1)  # a beat started at about 0.05 s and runs until about 0.2 s
    exited = time.monotonic()
    assert sqs.count() >= 1
    assert len(finished) == sqs.count()  # every beat that started has finished
    assert all(t <= exited for t in finished)


def test_exit_is_prompt_even_with_a_long_interval():
    """The worker's interval is 50 s; leaving the block must not wait for the next beat."""
    sqs = RecordingSqs()
    start = time.monotonic()
    with worker.Heartbeat(sqs, QUEUE, RECEIPT, interval_seconds=60):
        pass
    assert time.monotonic() - start < 2.0
    assert sqs.count() == 0


def test_an_exception_in_the_block_passes_out_and_stops_the_beats():
    sqs = RecordingSqs()
    with pytest.raises(ValueError, match="record failed"):
        with worker.Heartbeat(sqs, QUEUE, RECEIPT, interval_seconds=0.05):
            time.sleep(0.2)
            raise ValueError("record failed")
    after_exit = sqs.count()
    time.sleep(0.3)
    assert sqs.count() == after_exit


# ---------------------------------------------------------------- release


def test_release_sets_visibility_to_zero(aws):
    aws.sqs.send_message(QueueUrl=aws.queue_url, MessageBody="x")
    msg = aws.sqs.receive_message(QueueUrl=aws.queue_url, VisibilityTimeout=120)["Messages"][0]
    assert aws.sqs.receive_message(QueueUrl=aws.queue_url).get("Messages") is None  # hidden

    worker.release(aws.sqs, aws.queue_url, msg["ReceiptHandle"])

    again = aws.sqs.receive_message(QueueUrl=aws.queue_url, VisibilityTimeout=0)
    assert len(again.get("Messages", [])) == 1  # visible again at once


def test_release_calls_change_message_visibility_with_zero():
    sqs = RecordingSqs()
    worker.release(sqs, QUEUE, RECEIPT)
    assert [kwargs for _, kwargs in sqs.calls] == [
        {"QueueUrl": QUEUE, "ReceiptHandle": RECEIPT, "VisibilityTimeout": 0}
    ]


@pytest.mark.parametrize(
    "error",
    [
        ClientError(
            {"Error": {"Code": "ReceiptHandleIsInvalid", "Message": "expired"}},
            "ChangeMessageVisibility",
        ),
        EndpointConnectionError(endpoint_url="https://sqs.us-east-1.amazonaws.com"),
    ],
    ids=["expired_receipt", "nat_down"],
)
def test_release_logs_and_does_not_raise_when_the_call_fails(caplog, error):
    sqs = RecordingSqs(fail_first=1, error=error)
    with caplog.at_level("DEBUG"):
        assert worker.release(sqs, QUEUE, RECEIPT) is None
    assert sqs.count() == 1
    assert any(r.levelname in ("WARNING", "ERROR", "CRITICAL") for r in caplog.records)


def test_release_with_an_invalid_receipt_handle_on_moto_does_not_raise(aws, caplog):
    with caplog.at_level("DEBUG"):
        worker.release(aws.sqs, aws.queue_url, "not-a-real-receipt-handle")
    assert any(r.levelname in ("WARNING", "ERROR", "CRITICAL") for r in caplog.records)
