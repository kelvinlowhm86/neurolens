"""M3a Heartbeat `on_beat` (the worker's database touch). Written first from docs/M3a_spec.md §5
("Heartbeat gains an on_beat ...") and §10.

Each beat first extends SQS visibility, then calls on_beat, after releasing its lock. Exit waits
only for a visibility call already in progress, never for a running on_beat (a database call can
wait up to 60 s for Aurora to wake). Timing tests use tiny intervals and generous tolerances.
"""

import threading
import time

from neurolens import worker

QUEUE = "https://sqs.us-east-1.amazonaws.com/123456789012/neurolens-test-queue"
RECEIPT = "receipt-handle-1"


class Events:
    """One ordered log shared by the fake SQS and on_beat."""

    def __init__(self):
        self.log = []
        self.lock = threading.Lock()

    def add(self, name):
        with self.lock:
            self.log.append((name, time.monotonic()))

    def names(self):
        with self.lock:
            return [n for n, _ in self.log]

    def times(self, name):
        with self.lock:
            return [t for n, t in self.log if n == name]


class EventSqs:
    def __init__(self, events, call_seconds=0.0):
        self.events = events
        self.call_seconds = call_seconds

    def change_message_visibility(self, **kwargs):
        self.events.add("visibility")
        if self.call_seconds:
            time.sleep(self.call_seconds)
        return {}


def test_each_beat_extends_visibility_before_calling_on_beat():
    events = Events()
    with worker.Heartbeat(
        EventSqs(events),
        QUEUE,
        RECEIPT,
        interval_seconds=0.05,
        on_beat=lambda: events.add("on_beat"),
    ):
        time.sleep(0.6)
    names = events.names()
    assert names.count("on_beat") >= 3
    assert names[0] == "visibility"
    for i, name in enumerate(names):
        if name == "on_beat":
            assert names[i - 1] == "visibility", names  # every on_beat right after its beat


def test_on_beat_is_optional():
    events = Events()
    with worker.Heartbeat(EventSqs(events), QUEUE, RECEIPT, interval_seconds=0.05):
        time.sleep(0.3)
    assert events.names().count("visibility") >= 2


def test_no_on_beat_before_the_first_interval():
    events = Events()
    with worker.Heartbeat(
        EventSqs(events),
        QUEUE,
        RECEIPT,
        interval_seconds=0.6,
        on_beat=lambda: events.add("on_beat"),
    ):
        time.sleep(0.15)
        assert events.names() == []
        time.sleep(1.0)
        assert "on_beat" in events.names()


def test_an_on_beat_exception_is_logged_and_beats_continue(caplog):
    events = Events()

    def failing():
        events.add("on_beat")
        raise RuntimeError("database is waking")

    with caplog.at_level("DEBUG"):
        with worker.Heartbeat(
            EventSqs(events), QUEUE, RECEIPT, interval_seconds=0.05, on_beat=failing
        ):
            time.sleep(0.6)
    assert events.names().count("on_beat") >= 3
    assert events.names().count("visibility") >= 3
    assert any(r.levelname in ("WARNING", "ERROR", "CRITICAL") for r in caplog.records)


def test_exit_does_not_wait_for_a_slow_on_beat_and_no_visibility_call_follows_exit():
    """A touch can wait up to 60 s for Aurora: leaving the block must not wait behind it, and
    once exit has begun no visibility call may start (it could hide a released message)."""
    events = Events()
    started = threading.Event()
    finish = threading.Event()

    def slow_touch():
        events.add("on_beat")
        started.set()
        finish.wait(5)

    try:
        with worker.Heartbeat(
            EventSqs(events), QUEUE, RECEIPT, interval_seconds=0.05, on_beat=slow_touch
        ):
            assert started.wait(2), "on_beat never ran"
            exit_begins = time.monotonic()
        exit_seconds = time.monotonic() - exit_begins
    finally:
        finish.set()  # let the slow on_beat end
    assert exit_seconds < 0.5
    time.sleep(0.3)  # the beat thread is free again: any further beat would show up now
    assert all(t <= exit_begins for t in events.times("visibility"))
    assert events.names().count("on_beat") == 1
