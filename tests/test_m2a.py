"""M2a tests, written from docs/M2a_spec.md section 8 (and 4g, 6).

Covers: put_result (S3 results, never overwritten), the worker publishing through it and the
DUPLICATE outcome, and resolve_paths keeping absolute paths. M2b §1a removed the idle exit of
run() and its tests (no-idle-exit is now tested in tests/test_m2b_shutdown.py), and changed a
result that exists before any work from DUPLICATE to SKIPPED (§5). Calls use the §1a signatures.
"""

import contextlib
import json
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from neurolens import settings, storage, worker
from neurolens.worker import Outcome, handle_record, process_message

MODULES = ("neurolens.inference", "neurolens.worker")
MAX_RECEIVES = 2  # the job queue's maxReceiveCount (M2a §4h); every message here is receive 1


def no_heartbeat():
    """M2b §1a: handle_record's heartbeat factory. These tests do not exercise the heartbeat."""
    return contextlib.nullcontext()


@pytest.fixture(autouse=True)
def fake_on(monkeypatch):
    monkeypatch.setenv("FAKE_INFERENCE", "1")


@pytest.fixture(autouse=True)
def no_gpu(patch_everywhere):
    patch_everywhere("gpu_info", lambda: None, *MODULES)


def client_error(code, status, operation="PutObject"):
    return ClientError(
        {
            "Error": {"Code": code, "Message": "test"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation,
    )


class FailingPut:
    """Delegates to a boto3 client, but put_object raises the given error."""

    def __init__(self, inner, exc):
        self._inner = inner
        self._exc = exc

    def put_object(self, **kwargs):
        raise self._exc

    def __getattr__(self, name):
        return getattr(self._inner, name)


def stored(aws, job_id):
    body = aws.s3.get_object(Bucket=aws.bucket, Key=f"results/{job_id}.json")["Body"]
    return json.loads(body.read())


def s3_event(*pairs):
    return json.dumps(
        {
            "Records": [
                {"s3": {"bucket": {"name": b}, "object": {"key": k, "size": 1}}} for b, k in pairs
            ]
        }
    )


# ---------------------------------------------------------------- put_result
# The installed moto implements put_object(IfNoneMatch="*") (checked: the second write fails
# with PreconditionFailed), so these run against moto's real behaviour. The 409/500 cases use
# a wrapped client because moto cannot be made to raise them.


def test_put_result_writes_results_job_id_json_and_returns_true(aws):
    result = {"job_id": "j1", "duration_seconds": 3, "timesteps": [{"t": 0}]}
    assert storage.put_result(aws.s3, aws.bucket, "j1", result) is True
    assert stored(aws, "j1") == result


def test_put_result_never_replaces_an_existing_result(aws):
    first = {"job_id": "j1", "value": "first"}
    assert storage.put_result(aws.s3, aws.bucket, "j1", first) is True
    assert (
        storage.put_result(aws.s3, aws.bucket, "j1", {"job_id": "j1", "value": "second"}) is False
    )
    assert stored(aws, "j1") == first


def test_put_result_for_different_jobs_are_independent(aws):
    assert storage.put_result(aws.s3, aws.bucket, "a", {"v": 1}) is True
    assert storage.put_result(aws.s3, aws.bucket, "b", {"v": 2}) is True
    assert stored(aws, "a") == {"v": 1}
    assert stored(aws, "b") == {"v": 2}


@pytest.mark.parametrize(
    "code, status",
    [("ConditionalRequestConflict", 409), ("InternalError", 500), ("AccessDenied", 403)],
)
def test_put_result_propagates_any_other_s3_error(aws, code, status):
    s3 = FailingPut(aws.s3, client_error(code, status))
    with pytest.raises(ClientError) as info:
        storage.put_result(s3, aws.bucket, "j1", {"v": 1})
    assert info.value.response["Error"]["Code"] == code


def test_put_result_treats_a_simulated_412_as_already_exists(aws):
    s3 = FailingPut(aws.s3, client_error("PreconditionFailed", 412))
    assert storage.put_result(s3, aws.bucket, "j1", {"v": 1}) is False


# ---------------------------------------------------------------- worker publishes via put_result


def test_outcome_has_duplicate():
    assert "DUPLICATE" in Outcome.__members__
    assert Outcome.DUPLICATE not in (Outcome.DONE, Outcome.REJECTED, Outcome.GONE)


def test_worker_result_lands_in_s3_and_nothing_in_the_local_output_folder(
    aws, make_cfg, roi_masks_small, new_key, clip_path
):
    cfg = make_cfg()
    job_id, key = new_key()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)

    outcome = handle_record(
        aws.bucket, key, s3=aws.s3, cfg=cfg, roi_masks=roi_masks_small, heartbeat=no_heartbeat
    )

    assert outcome is Outcome.DONE
    assert stored(aws, job_id)["job_id"] == job_id
    assert list(Path(cfg["paths"]["output"]).iterdir()) == []


def test_worker_publishes_through_put_result(
    aws, make_cfg, roi_masks_small, new_key, clip_path, monkeypatch
):
    seen = []
    real = storage.put_result

    def spy(s3, bucket, job_id, result):
        seen.append((bucket, job_id, result["job_id"]))
        return real(s3, bucket, job_id, result)

    monkeypatch.setattr(storage, "put_result", spy)
    job_id, key = new_key()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    handle_record(
        aws.bucket,
        key,
        s3=aws.s3,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        heartbeat=no_heartbeat,
    )
    assert seen == [(aws.bucket, job_id, job_id)]


def test_existing_result_gives_skipped_and_is_left_unchanged(
    aws, make_cfg, roi_masks_small, new_key, clip_path
):
    """M2b §5 changed the outcome: a result that exists before any work is SKIPPED (was
    DUPLICATE in M2a). The result is still left unchanged."""
    job_id, key = new_key()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    earlier = {"job_id": job_id, "marker": "written by an earlier run"}
    assert storage.put_result(aws.s3, aws.bucket, job_id, earlier) is True

    outcome = handle_record(
        aws.bucket,
        key,
        s3=aws.s3,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        heartbeat=no_heartbeat,
    )

    assert outcome is Outcome.SKIPPED
    assert stored(aws, job_id) == earlier


def test_duplicate_when_put_result_returns_false(
    aws, make_cfg, roi_masks_small, new_key, clip_path, patch_everywhere
):
    patch_everywhere("put_result", lambda *a, **kw: False, "neurolens.storage", "neurolens.worker")
    _, key = new_key()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    outcome = handle_record(
        aws.bucket,
        key,
        s3=aws.s3,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        heartbeat=no_heartbeat,
    )
    assert outcome is Outcome.DUPLICATE


def test_duplicate_message_is_deleted(
    aws, make_cfg, roi_masks_small, new_key, clip_path, queue_message, remaining
):
    """DUPLICATE is final: a redelivered notice for an already-finished job leaves the queue."""
    job_id, key = new_key()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    storage.put_result(aws.s3, aws.bucket, job_id, {"job_id": job_id, "marker": "first"})

    process_message(
        queue_message(s3_event((aws.bucket, key))),
        s3=aws.s3,
        sqs=aws.sqs,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        shutdown=worker.ShutdownSignal(),
        max_receives=MAX_RECEIVES,
    )

    assert remaining() == []
    assert stored(aws, job_id)["marker"] == "first"


def test_a_put_result_error_leaves_the_message_for_retry(
    aws, make_cfg, roi_masks_small, new_key, clip_path, queue_message, remaining, patch_everywhere
):
    def boom(*a, **kw):
        raise client_error("ConditionalRequestConflict", 409)

    patch_everywhere("put_result", boom, "neurolens.storage", "neurolens.worker")
    _, key = new_key()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    process_message(
        queue_message(s3_event((aws.bucket, key))),
        s3=aws.s3,
        sqs=aws.sqs,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        shutdown=worker.ShutdownSignal(),
        max_receives=MAX_RECEIVES,
    )
    assert len(remaining()) == 1


# ---------------------------------------------------------------- resolve_paths


def test_resolve_paths_keeps_absolute_paths_unchanged(tmp_path):
    cfg = {
        "paths": {
            "models": "/opt/neurolens/cache/models",
            "data": "/opt/neurolens/cache/data",
            "output": "/opt/neurolens/output",
        }
    }
    paths = settings.resolve_paths(cfg, tmp_path)
    assert paths["models"] == Path("/opt/neurolens/cache/models")
    assert paths["data"] == Path("/opt/neurolens/cache/data")
    assert paths["output"] == Path("/opt/neurolens/output")


def test_resolve_paths_still_resolves_relative_paths_under_the_root(tmp_path):
    cfg = {"paths": {"models": "./models", "data": "data", "output": "out/results"}}
    paths = settings.resolve_paths(cfg, tmp_path)
    assert paths["models"] == (tmp_path / "models").resolve()
    assert paths["data"] == (tmp_path / "data").resolve()
    assert paths["output"] == (tmp_path / "out" / "results").resolve()


def test_resolve_paths_handles_a_mix_of_absolute_and_relative(tmp_path):
    cfg = {"paths": {"models": "/opt/neurolens/cache/models", "data": "data", "output": "out"}}
    paths = settings.resolve_paths(cfg, tmp_path)
    assert paths["models"] == Path("/opt/neurolens/cache/models")
    assert paths["data"] == (tmp_path / "data").resolve()
