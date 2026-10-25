"""M3f worker storage split (docs/M3f_spec.md §5b, §10 "Worker").

A consented job keeps its video under `dataset/<job_id>/` and writes NeuroLens's own output
(`result.json` and `brain`) under `academic/<job_id>/`, never under `dataset/`. The worker records
the video's SHA-256 before the copy, so it survives a copy that fails.
"""

import contextlib
import hashlib
import uuid
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from conftest import STARTER_CENTS, USER_ID
from neurolens import billing, settings, worker

MODULES = ("neurolens.inference", "neurolens.worker")


@pytest.fixture(autouse=True)
def fake_on(monkeypatch):
    monkeypatch.setenv("FAKE_INFERENCE", "1")


@pytest.fixture(autouse=True)
def no_gpu(patch_everywhere):
    patch_everywhere("gpu_info", lambda: None, *MODULES)


def make_job(db, *, consent=True):
    job_id = str(uuid.uuid4())
    key = f"uploads/{USER_ID}/{job_id}.mp4"
    billing.ensure_user(db, USER_ID, f"{USER_ID}@example.com", STARTER_CENTS)
    billing.reserve(db, USER_ID, job_id, key, "clip.mp4", 3000, research_consent=consent)
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


def prefix_keys(aws, prefix):
    pages = aws.s3.get_paginator("list_objects_v2").paginate(Bucket=aws.bucket, Prefix=prefix)
    return [obj["Key"] for page in pages for obj in page.get("Contents", [])]


class CopyFails:
    def __init__(self, inner):
        self._inner = inner

    def copy_object(self, *args, **kwargs):
        raise ClientError(
            {"Error": {"Code": "InternalError", "Message": "copy failed"}}, "CopyObject"
        )

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_a_consented_job_splits_video_and_output_between_dataset_and_academic(
    aws, db, pg, make_cfg, roi_masks_small, clip_path
):
    job_id, key = make_job(db)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)

    assert run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small) is worker.Outcome.DONE

    dataset = prefix_keys(aws, f"dataset/{job_id}/")
    assert dataset == [f"dataset/{job_id}/video.mp4"]
    assert object_bytes(aws, f"dataset/{job_id}/video.mp4") == Path(clip_path).read_bytes()

    for name in ("result.json", "brain"):
        assert object_exists(aws, f"academic/{job_id}/{name}")
    result = object_bytes(aws, f"academic/{job_id}/result.json")
    assert result == object_bytes(aws, f"results/{job_id}.json")
    assert object_bytes(aws, f"academic/{job_id}/brain") == object_bytes(
        aws, f"results/{job_id}.brain"
    )


def test_the_video_hash_is_recorded(aws, db, pg, make_cfg, roi_masks_small, clip_path):
    job_id, key = make_job(db)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)

    assert run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small) is worker.Outcome.DONE

    expected = hashlib.sha256(Path(clip_path).read_bytes()).hexdigest()
    assert pg.job(job_id)["video_sha256"] == expected


def test_the_hash_is_recorded_before_a_copy_that_fails(
    aws, db, pg, make_cfg, roi_masks_small, clip_path
):
    job_id, key = make_job(db)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)

    outcome = run_job(
        aws, db, job_id, clip_path, make_cfg(), roi_masks_small, s3=CopyFails(aws.s3)
    )

    assert outcome is worker.Outcome.FAILED
    expected = hashlib.sha256(Path(clip_path).read_bytes()).hexdigest()
    assert pg.job(job_id)["video_sha256"] == expected
    assert not object_exists(aws, f"academic/{job_id}/result.json")
