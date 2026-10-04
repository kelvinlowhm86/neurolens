"""worker.boot_record (pure): the UTC timestamps a GPU boot's own machine saw. Written from
docs/M2b_spec.md §1a and §11. No durations here: cold_start.py computes them (it alone knows the
launch time, from AWS's scaling history).

FIXTURE: tests/fixtures/neurolens-boot-log.txt is CONSTRUCTED, not copied from a machine: no
real log was available when these tests were written. Its lines follow the exact format of
the `log` function in infra/terraform/worker_userdata.sh.tftpl (`date -u +%Y-%m-%dT%H:%M:%SZ`
then the step text), with M2a's measured weight sync (77 s). Replace it with a real copy of
/var/log/neurolens-boot.log from the first M2b GPU worker (§11) and update the expected
timestamps below to that log's. (Named .txt because .gitignore ignores *.log.)
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from neurolens import worker

BOOT_LOG = Path(__file__).parent / "fixtures" / "neurolens-boot-log.txt"
READY = datetime(2026, 10, 14, 3, 5, 58, tzinfo=UTC)
FIELDS = {
    "instance_id",
    "instance_type",
    "userdata_start_utc",
    "weight_sync_start_utc",
    "weight_sync_end_utc",
    "worker_start_utc",
    "ready_utc",
}

# A boot whose log has no weight sync (fake mode skips it, §12) and that never started the
# worker (the boot stopped after the code pull).
PARTIAL_LOG = """\
2026-10-14T05:00:31Z boot start
2026-10-14T05:00:31Z no instance-store disk (CPU rehearsal): using a folder on the root disk
2026-10-14T05:00:32Z settings written
2026-10-14T05:00:40Z code 83e0bf9 pulled and settings verified
"""


@pytest.fixture
def record():
    return worker.boot_record(BOOT_LOG.read_text(), READY, "i-0123456789abcdef0", "g6e.2xlarge")


def test_boot_record_returns_exactly_the_machine_seen_fields(record):
    assert set(record) == FIELDS


def test_boot_record_identifies_the_machine(record):
    assert record["instance_id"] == "i-0123456789abcdef0"
    assert record["instance_type"] == "g6e.2xlarge"


def test_boot_record_gives_each_step_timestamp(record):
    assert record["userdata_start_utc"] == "2026-10-14T03:00:41Z"  # the first boot-log line
    assert record["weight_sync_start_utc"] == "2026-10-14T03:00:42Z"
    assert record["weight_sync_end_utc"] == "2026-10-14T03:01:59Z"
    assert record["worker_start_utc"] == "2026-10-14T03:02:09Z"
    assert record["ready_utc"] == "2026-10-14T03:05:58Z"  # the given datetime, in §4's format


def test_a_missing_step_gives_none():
    record = worker.boot_record(PARTIAL_LOG, READY, "i-0fedcba9876543210", "t3.large")
    assert set(record) == FIELDS
    assert record["userdata_start_utc"] == "2026-10-14T05:00:31Z"
    assert record["weight_sync_start_utc"] is None
    assert record["weight_sync_end_utc"] is None
    assert record["worker_start_utc"] is None
    assert record["ready_utc"] == "2026-10-14T03:05:58Z"


def test_boot_record_is_pure_and_repeatable():
    text = BOOT_LOG.read_text()
    first = worker.boot_record(text, READY, "i-0123456789abcdef0", "g6e.2xlarge")
    second = worker.boot_record(text, READY, "i-0123456789abcdef0", "g6e.2xlarge")
    assert first == second
    assert BOOT_LOG.read_text() == text
