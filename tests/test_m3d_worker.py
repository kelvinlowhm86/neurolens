"""M3d §5 worker and storage: the brain file's bytes, its write order, and its failures.

Full treatment because a missed write order could publish a result without its brain file, or
leave user data behind after a failed run.
"""

import contextlib
import json
import uuid

import numpy as np
import pytest
from botocore.exceptions import ClientError
from conftest import STARTER_CENTS, USER_ID
from neurolens import billing, settings, storage, worker

MODULES = ("neurolens.inference", "neurolens.worker")


@pytest.fixture(autouse=True)
def fake_on(monkeypatch):
    monkeypatch.setenv("FAKE_INFERENCE", "1")


@pytest.fixture(autouse=True)
def no_gpu(patch_everywhere):
    patch_everywhere("gpu_info", lambda: None, *MODULES)


def make_job(db, *, consent=False):
    job_id = str(uuid.uuid4())
    key = f"uploads/{USER_ID}/{job_id}.mp4"
    billing.ensure_user(db, USER_ID, f"{USER_ID}@example.com", STARTER_CENTS)
    billing.reserve(
        db,
        USER_ID,
        job_id,
        key,
        "clip.mp4",
        3000,
        research_consent=consent,
    )
    return job_id, key


def verify(db, job_id):
    assert billing.verify_queued(db, job_id, 3000, settings.max_duration(None)) == "ok"


def run_job(aws, db, job_id, clip_path, cfg, masks, *, s3=None):
    return worker.handle_job(
        job_id,
        str(clip_path),
        s3=s3 or aws.s3,
        db=db,
        cfg=cfg,
        roi_masks=masks,
        keep_alive=lambda on_beat: contextlib.nullcontext(),
    )


def object_bytes(aws, key):
    return aws.s3.get_object(Bucket=aws.bucket, Key=key)["Body"].read()


def object_exists(aws, key):
    try:
        aws.s3.head_object(Bucket=aws.bucket, Key=key)
    except ClientError as err:
        assert err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound")
        return False
    return True


def written_keys(s3):
    keys = []
    for name, args, kwargs in s3.calls:
        if name not in ("put_object", "copy_object"):
            continue
        keys.append(kwargs.get("Key") or (args[1] if len(args) > 1 else None))
    return keys


# ---------------------------------------------------------------- brain_bytes / put_brain


def test_brain_bytes_is_float16_little_endian_row_major():
    preds = np.array([[1.5, -2.25], [0.0, 3.5]], dtype=np.float32)

    data = storage.brain_bytes(preds)

    assert data == preds.astype("<f2").tobytes(order="C")
    assert len(data) == preds.size * 2


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_brain_bytes_refuses_non_finite_values(value):
    with pytest.raises(ValueError):
        storage.brain_bytes(np.array([[value]], dtype=np.float32))


def test_brain_bytes_refuses_a_float16_overflow():
    with pytest.raises(ValueError):
        storage.brain_bytes(np.array([[1e6]], dtype=np.float32))


def test_put_brain_writes_results_job_id_brain(aws):
    preds = np.array([[1.5, -2.25]], dtype=np.float32)

    storage.put_brain(aws.s3, aws.bucket, "job-1", preds)

    assert object_bytes(aws, "results/job-1.brain") == storage.brain_bytes(preds)


@pytest.mark.parametrize("value", [np.nan, np.inf, 1e6])
def test_put_brain_refuses_a_bad_value_before_writing(aws, value):
    aws.s3.put_object(Bucket=aws.bucket, Key="results/job-1.brain", Body=b"earlier")

    with pytest.raises(ValueError):
        storage.put_brain(aws.s3, aws.bucket, "job-1", np.array([[value]], dtype=np.float32))

    assert object_bytes(aws, "results/job-1.brain") == b"earlier"


def test_put_dataset_writes_the_given_brain_bytes(aws):
    job_id, key = "job-1", "uploads/user/job-1.mp4"
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"video")
    result = {"format": 2, "duration_seconds": 0, "signals": {}, "muted": {}}

    storage.put_dataset(aws.s3, aws.bucket, job_id, key, result, brain=b"brain bytes")

    assert object_bytes(aws, f"dataset/{job_id}/brain") == b"brain bytes"


# ---------------------------------------------------------------- the worker's write order


def test_the_brain_is_written_before_the_result(
    aws, db, make_cfg, roi_masks_small, clip_path, spy_s3
):
    job_id, key = make_job(db)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)
    s3 = spy_s3()

    assert (
        run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small, s3=s3)
        is worker.Outcome.DONE
    )

    keys = written_keys(s3)
    brain = keys.index(f"results/{job_id}.brain")
    published = keys.index(f"results/{job_id}.json")
    assert brain < published


class BrainWriteFails:
    def __init__(self, inner):
        self._inner = inner

    def put_object(self, **kwargs):
        if str(kwargs.get("Key", "")).endswith(".brain"):
            raise ClientError(
                {"Error": {"Code": "InternalError", "Message": "brain write failed"}},
                "PutObject",
            )
        return self._inner.put_object(**kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_a_failed_brain_write_publishes_no_result_and_refunds(
    aws, db, pg, make_cfg, roi_masks_small, clip_path
):
    job_id, key = make_job(db)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)

    outcome = run_job(
        aws, db, job_id, clip_path, make_cfg(), roi_masks_small, s3=BrainWriteFails(aws.s3)
    )

    assert outcome is worker.Outcome.FAILED
    assert not object_exists(aws, f"results/{job_id}.json")
    assert not object_exists(aws, f"results/{job_id}.brain")
    job = pg.job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "processing_failed"
    assert pg.balance() == (STARTER_CENTS, 0)


def test_a_float16_overflow_fails_the_job_before_any_result_is_published(
    aws, db, pg, make_cfg, roi_masks_small, clip_path, patch_everywhere
):
    job_id, key = make_job(db)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)

    def overflowing(events, duration):
        return np.full((3, 20484), 1e6)

    patch_everywhere("predict", overflowing, *MODULES)

    assert run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small) is worker.Outcome.FAILED

    assert not object_exists(aws, f"results/{job_id}.brain")
    assert not object_exists(aws, f"results/{job_id}.json")
    assert pg.job(job_id)["status"] == "failed"


def test_a_consented_job_writes_the_same_brain_bytes_to_the_dataset(
    aws, db, make_cfg, roi_masks_small, clip_path
):
    job_id, key = make_job(db, consent=True)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)

    assert run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small) is worker.Outcome.DONE

    brain = object_bytes(aws, f"results/{job_id}.brain")
    assert object_bytes(aws, f"dataset/{job_id}/brain") == brain
    stored = json.loads(object_bytes(aws, f"results/{job_id}.json"))
    assert stored["format"] == 2
