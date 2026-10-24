"""M2a tests, written from docs/M2a_spec.md section 8 (and 4g, 6).

Covers: put_result (S3 results, never overwritten), the worker publishing through it and the
DUPLICATE outcome, and resolve_paths keeping absolute paths. M2b §1a removed the idle exit of
run() and its tests, and changed a result that exists before any work from DUPLICATE to SKIPPED
(§5). Calls use the M3a §5 signatures (db, heartbeat(on_beat)), with each record's job seeded in
PostgreSQL first.
"""

import contextlib
import json
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from neurolens import billing, settings, storage, worker
from neurolens.worker import Outcome

MODULES = ("neurolens.inference", "neurolens.worker")


def no_keep_alive(on_beat):
    return contextlib.nullcontext()


def verify(db, job_id):
    assert billing.verify_queued(db, job_id, 3000, settings.max_duration(None)) == "ok"


def run_job(aws, db, job_id, clip_path, cfg, masks):
    return worker.handle_job(
        job_id,
        str(clip_path),
        s3=aws.s3,
        db=db,
        cfg=cfg,
        roi_masks=masks,
        keep_alive=no_keep_alive,
    )


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


# ---------------------------------------------------------------- put_result
# The installed moto implements put_object(IfNoneMatch="*") (checked: the second write fails
# with PreconditionFailed), so these run against moto's real behaviour. The 409/500 cases use
# a wrapped client because moto cannot be made to raise them.


def test_put_result_writes_results_job_id_json_and_returns_true(aws):
    result = {
        "format": 2,
        "job_id": "j1",
        "duration_seconds": 0,
        "signals": {},
        "muted": {},
    }
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
    assert Outcome.DUPLICATE not in (Outcome.DONE, Outcome.REJECTED, Outcome.SKIPPED)


def test_worker_result_lands_in_s3(aws, make_cfg, roi_masks_small, db, new_job, clip_path):
    cfg = make_cfg()
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)

    outcome = run_job(aws, db, job_id, clip_path, cfg, roi_masks_small)

    assert outcome is Outcome.DONE
    assert stored(aws, job_id)["job_id"] == job_id


def test_worker_publishes_through_put_result(
    aws, make_cfg, roi_masks_small, db, new_job, clip_path, monkeypatch
):
    seen = []
    real = storage.put_result

    def spy(s3, bucket, job_id, result):
        seen.append((bucket, job_id, result["job_id"]))
        return real(s3, bucket, job_id, result)

    monkeypatch.setattr(storage, "put_result", spy)
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)
    run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small)
    assert seen == [(aws.bucket, job_id, job_id)]


def test_existing_result_gives_skipped_and_is_left_unchanged(
    aws, make_cfg, roi_masks_small, db, new_job, clip_path
):
    """M2b §5 changed the outcome: a result that exists before any work is SKIPPED (was
    DUPLICATE in M2a). The result is still left unchanged."""
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    earlier = {"job_id": job_id, "marker": "written by an earlier run"}
    assert storage.put_result(aws.s3, aws.bucket, job_id, earlier) is True

    outcome = run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small)

    assert outcome is Outcome.SKIPPED
    assert stored(aws, job_id) == earlier


def test_duplicate_when_put_result_returns_false(
    aws, make_cfg, roi_masks_small, db, new_job, pg, clip_path, patch_everywhere
):
    patch_everywhere("put_result", lambda *a, **kw: False, "neurolens.storage", "neurolens.worker")
    job_id, key = new_job()
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)
    outcome = run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small)
    assert outcome is Outcome.DUPLICATE
    assert pg.job(job_id)["status"] == "done"  # settled all the same


# ---------------------------------------------------------------- resolve_paths


def test_resolve_paths_keeps_absolute_paths_unchanged(tmp_path):
    cfg = {
        "paths": {
            "models": "/opt/neurolens/cache/models",
            "data": "/opt/neurolens/cache/data",
        }
    }
    paths = settings.resolve_paths(cfg, tmp_path)
    assert paths["models"] == Path("/opt/neurolens/cache/models")
    assert paths["data"] == Path("/opt/neurolens/cache/data")


def test_resolve_paths_still_resolves_relative_paths_under_the_root(tmp_path):
    cfg = {"paths": {"models": "./models", "data": "data"}}
    paths = settings.resolve_paths(cfg, tmp_path)
    assert paths["models"] == (tmp_path / "models").resolve()
    assert paths["data"] == (tmp_path / "data").resolve()


def test_resolve_paths_handles_a_mix_of_absolute_and_relative(tmp_path):
    cfg = {"paths": {"models": "/opt/neurolens/cache/models", "data": "data"}}
    paths = settings.resolve_paths(cfg, tmp_path)
    assert paths["models"] == Path("/opt/neurolens/cache/models")
    assert paths["data"] == (tmp_path / "data").resolve()
