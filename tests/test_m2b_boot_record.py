"""The GPU container's boot record (docs/M3e_spec.md §10).

At start-up the container writes `experiments/boots/<host>-<start time>.json` with four UTC
timestamps: `container_start_utc`, `model_loaded_utc`, `warmup_done_utc` and `ready_utc`. The
record is built from the times the start-up steps saw; `cold_start.py` joins it with Application
Auto Scaling's scaling activities.
"""

from datetime import UTC, datetime

from neurolens import worker

CONTAINER_START = datetime(2026, 10, 14, 3, 0, 41, tzinfo=UTC)
MODEL_LOADED = datetime(2026, 10, 14, 3, 2, 9, tzinfo=UTC)
WARMUP_DONE = datetime(2026, 10, 14, 3, 3, 55, tzinfo=UTC)
READY = datetime(2026, 10, 14, 3, 5, 58, tzinfo=UTC)
GPU_NAME = "NVIDIA L4"
FIELDS = {
    "container_start_utc",
    "model_loaded_utc",
    "warmup_done_utc",
    "ready_utc",
    "gpu_name",
}


def test_boot_record_returns_exactly_the_container_fields():
    assert (
        set(worker.boot_record(CONTAINER_START, MODEL_LOADED, WARMUP_DONE, READY, GPU_NAME))
        == FIELDS
    )


def test_boot_record_formats_each_step_as_utc_text():
    record = worker.boot_record(CONTAINER_START, MODEL_LOADED, WARMUP_DONE, READY, GPU_NAME)

    assert record["container_start_utc"] == "2026-10-14T03:00:41Z"
    assert record["model_loaded_utc"] == "2026-10-14T03:02:09Z"
    assert record["warmup_done_utc"] == "2026-10-14T03:03:55Z"
    assert record["ready_utc"] == "2026-10-14T03:05:58Z"


def test_boot_record_keeps_a_missing_step_as_none():
    record = worker.boot_record(CONTAINER_START, None, None, READY, GPU_NAME)

    assert set(record) == FIELDS
    assert record["container_start_utc"] == "2026-10-14T03:00:41Z"
    assert record["model_loaded_utc"] is None
    assert record["warmup_done_utc"] is None
    assert record["ready_utc"] == "2026-10-14T03:05:58Z"


def test_boot_record_is_pure_and_repeatable():
    first = worker.boot_record(CONTAINER_START, MODEL_LOADED, WARMUP_DONE, READY, GPU_NAME)
    second = worker.boot_record(CONTAINER_START, MODEL_LOADED, WARMUP_DONE, READY, GPU_NAME)
    assert first == second
    assert CONTAINER_START.tzinfo is UTC  # the inputs were not mutated
