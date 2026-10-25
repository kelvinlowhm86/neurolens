"""M3c research consent (docs/M3c_spec.md §2, §3a, §3b, §8).

The 003 migration, the consent flag stored by `billing.reserve` and the presign route, and the
worker's dataset copy: only a consented job is copied, the copy happens before `put_result`, a
failed copy fails the job without settling it, and a worker that lost its claim writes nothing.
"""

import contextlib
import json
import uuid
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from conftest import MIGRATIONS, STARTER_CENTS, USER_ID
from neurolens import billing, settings, storage, worker
from neurolens.web.app import create_app

MODULES = ("neurolens.inference", "neurolens.worker")


@pytest.fixture
def app_for(aws, db, make_cfg, tmp_path):
    def make(s3_client=None, **overrides):
        app = create_app(
            cfg=make_cfg(**overrides),
            data_dir=tmp_path / "data",
            db=db,
            s3_client=s3_client or aws.s3,
        )
        app.config["TESTING"] = True
        return app.test_client()

    return make


@pytest.fixture
def client(app_for):
    return app_for()


@pytest.fixture(autouse=True)
def fake_on(monkeypatch):
    monkeypatch.setenv("FAKE_INFERENCE", "1")


@pytest.fixture(autouse=True)
def no_gpu(patch_everywhere):
    patch_everywhere("gpu_info", lambda: None, *MODULES)


def presign_body(**overrides):
    data = {
        "filename": "ad.mp4",
        "content_type": "video/mp4",
        "client_duration_seconds": 3,
        "client_declared_bytes": 1024,
    }
    data.update(overrides)
    return data


def make_job(db, *, consent, ext=".mp4", user_id=USER_ID, client_seconds=3):
    """A job reserved exactly as presign leaves it, with the given consent flag."""
    job_id = str(uuid.uuid4())
    key = f"uploads/{user_id}/{job_id}{ext}"
    billing.ensure_user(db, user_id, f"{user_id}@example.com", STARTER_CENTS)
    billed = billing.reserve(
        db,
        user_id,
        job_id,
        key,
        f"clip{ext}",
        round(client_seconds * 1000),
        research_consent=consent,
    )
    assert billed > 0
    return job_id, key


def object_exists(aws, key):
    try:
        aws.s3.head_object(Bucket=aws.bucket, Key=key)
    except ClientError as err:
        assert err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound")
        return False
    return True


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


def dataset_keys(aws, job_id):
    prefix = f"dataset/{job_id}/"
    resp = aws.s3.list_objects_v2(Bucket=aws.bucket, Prefix=prefix)
    return [obj["Key"] for obj in resp.get("Contents", [])]


# ---------------------------------------------------------------- migration 003


def test_migration_003_adds_the_consent_and_deletion_columns_and_drops_stripe_events(pg):
    assert (MIGRATIONS / "003_academic.sql").is_file()
    columns = {
        row["column_name"]: row
        for row in pg.rows(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'jobs'"
        )
    }
    consent = columns["research_consent"]
    assert consent["data_type"] == "boolean"
    assert consent["is_nullable"] == "NO"
    assert consent["column_default"].lower() == "false"
    deleted = columns["deleted_at"]
    assert deleted["data_type"] == "timestamp with time zone"
    assert deleted["is_nullable"] == "YES"
    assert deleted["column_default"] is None
    tables = {
        row["table_name"]
        for row in pg.rows(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
        )
    }
    assert "stripe_test_events" not in tables


# ---------------------------------------------------------------- the reserve interface


def test_reserve_defaults_research_consent_to_false(db, pg):
    job_id = str(uuid.uuid4())
    key = f"uploads/{USER_ID}/{job_id}.mp4"
    billing.ensure_user(db, USER_ID, f"{USER_ID}@example.com", STARTER_CENTS)
    billing.reserve(db, USER_ID, job_id, key, "clip.mp4", 3000)
    assert pg.job(job_id)["research_consent"] is False


def test_reserve_stores_research_consent_true(db, pg):
    job_id, _ = make_job(db, consent=True)
    assert pg.job(job_id)["research_consent"] is True


def test_job_state_returns_research_consent(db):
    job_id, _ = make_job(db, consent=True)
    assert billing.job_state(db, job_id)["research_consent"] is True


# ---------------------------------------------------------------- presign


@pytest.mark.parametrize(
    "consent, expected",
    [(True, True), (False, False), (None, False)],
    ids=["true", "false", "absent"],
)
def test_presign_stores_the_consent_box(client, pg, consent, expected):
    body = presign_body()
    if consent is not None:
        body["research_consent"] = consent
    if consent:
        body["consent_version"] = billing.CONSENT_VERSION
    resp = client.post("/api/uploads/presign", json=body)
    assert resp.status_code == 200
    assert pg.job(resp.get_json()["job_id"])["research_consent"] is expected


@pytest.mark.parametrize(
    "value", ["true", "false", 1, 0, None, [], {"yes": True}], ids=lambda v: repr(v)
)
def test_presign_refuses_a_non_boolean_consent_and_reserves_nothing(client, pg, value):
    resp = client.post("/api/uploads/presign", json=presign_body(research_consent=value))
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_request"
    assert pg.rows("SELECT job_id FROM jobs") == []


# ---------------------------------------------------------------- the dataset copy in the worker


def test_a_consented_job_is_copied_to_the_dataset(
    aws, db, pg, make_cfg, roi_masks_small, clip_path
):
    job_id, key = make_job(db, consent=True)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)
    outcome = run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small)
    assert outcome is worker.Outcome.DONE
    video = f"dataset/{job_id}/video.mp4"
    result_key = f"academic/{job_id}/result.json"
    assert object_exists(aws, video) and object_exists(aws, result_key)
    body = aws.s3.get_object(Bucket=aws.bucket, Key=video)["Body"].read()
    assert body == Path(clip_path).read_bytes()
    result = json.loads(aws.s3.get_object(Bucket=aws.bucket, Key=result_key)["Body"].read())
    assert result["job_id"] == job_id
    assert pg.job(job_id)["status"] == "done"


def test_an_unconsented_job_writes_nothing_under_dataset(
    aws, db, pg, make_cfg, roi_masks_small, clip_path
):
    job_id, key = make_job(db, consent=False)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)
    assert run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small) is worker.Outcome.DONE
    assert dataset_keys(aws, job_id) == []
    assert object_exists(aws, f"results/{job_id}.json")


def test_a_failed_dataset_copy_fails_and_refunds_the_job(
    aws, db, pg, make_cfg, roi_masks_small, clip_path
):
    job_id, key = make_job(db, consent=True)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)

    class CopyFails:
        def __init__(self, inner):
            self._inner = inner

        def copy_object(self, *args, **kwargs):
            raise ClientError(
                {"Error": {"Code": "InternalError", "Message": "copy failed"}}, "CopyObject"
            )

        def __getattr__(self, name):
            return getattr(self._inner, name)

    outcome = run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small, s3=CopyFails(aws.s3))
    assert outcome is worker.Outcome.FAILED
    job = pg.job(job_id)
    assert job["status"] == "failed" and job["captured_cents"] is None
    assert job["error_code"] == "processing_failed"
    assert pg.balance() == (STARTER_CENTS, 0)
    assert not object_exists(aws, f"results/{job_id}.json")


def test_the_dataset_is_written_before_put_result(
    aws, db, make_cfg, roi_masks_small, clip_path, spy_s3
):
    job_id, key = make_job(db, consent=True)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)
    s3 = spy_s3()
    assert (
        run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small, s3=s3)
        is worker.Outcome.DONE
    )

    def written_key(call):
        name, args, kwargs = call
        if name not in ("put_object", "copy_object"):
            return None
        return kwargs.get("Key") or (args[1] if len(args) > 1 else None)

    written = [written_key(call) for call in s3.calls]
    video = written.index(f"dataset/{job_id}/video.mp4")
    dataset_result = written.index(f"academic/{job_id}/result.json")
    published = written.index(f"results/{job_id}.json")
    assert video < published and dataset_result < published


def test_a_worker_that_lost_its_claim_before_saving_writes_nothing_to_the_dataset(
    aws, db, pg, make_cfg, roi_masks_small, clip_path, patch_everywhere
):
    job_id, key = make_job(db, consent=True)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)
    from neurolens import response

    real_extract = response.extract_response

    def extract_then_lose(*args, **kwargs):
        result = real_extract(*args, **kwargs)
        pg.age(job_id, updated_s=120)  # this worker looked dead ...
        assert billing.claim(db, job_id) == 2  # ... and another worker took the job
        return result

    patch_everywhere(
        "extract_response", extract_then_lose, "neurolens.response", "neurolens.worker"
    )
    assert (
        run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small)
        is worker.Outcome.LOST_CLAIM
    )
    assert dataset_keys(aws, job_id) == []
    assert not object_exists(aws, f"results/{job_id}.json")
    job = pg.job(job_id)
    assert job["status"] == "processing" and job["attempt"] == 2


def test_an_unconsented_worker_that_lost_its_claim_writes_nothing(
    aws, db, pg, make_cfg, roi_masks_small, clip_path, patch_everywhere
):
    job_id, key = make_job(db, consent=False)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)
    from neurolens import response

    real_extract = response.extract_response

    def extract_then_lose(*args, **kwargs):
        result = real_extract(*args, **kwargs)
        pg.age(job_id, updated_s=120)  # this worker looked dead ...
        assert billing.claim(db, job_id) == 2  # ... and another worker took the job
        return result

    patch_everywhere(
        "extract_response", extract_then_lose, "neurolens.response", "neurolens.worker"
    )
    assert (
        run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small)
        is worker.Outcome.LOST_CLAIM
    )
    assert dataset_keys(aws, job_id) == []
    assert not object_exists(aws, f"results/{job_id}.json")
    job = pg.job(job_id)
    assert job["status"] == "processing" and job["attempt"] == 2
    assert job["captured_cents"] is None


# ---------------------------------------------------------------- storage.put_dataset


def test_put_dataset_copies_the_video_with_its_extension_and_stores_the_result(aws, new_key):
    job_id, key = new_key(".mov")
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"the video")
    result = {"format": 2, "job_id": job_id, "duration_seconds": 0, "signals": {}, "muted": {}}
    storage.put_dataset(aws.s3, aws.bucket, job_id, key, result, brain=b"brain bytes")
    video = aws.s3.get_object(Bucket=aws.bucket, Key=f"dataset/{job_id}/video.mov")["Body"]
    assert video.read() == b"the video"
    stored = aws.s3.get_object(Bucket=aws.bucket, Key=f"academic/{job_id}/result.json")["Body"]
    assert json.loads(stored.read()) == result
    brain = aws.s3.get_object(Bucket=aws.bucket, Key=f"academic/{job_id}/brain")["Body"]
    assert brain.read() == b"brain bytes"


def test_put_dataset_refuses_a_nan_result(aws, new_key):
    job_id, key = new_key()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"the video")
    with pytest.raises(ValueError):
        storage.put_dataset(aws.s3, aws.bucket, job_id, key, {"bad": float("nan")}, brain=b"x")
    assert not object_exists(aws, f"dataset/{job_id}/result.json")


def test_put_dataset_replaces_an_earlier_copy(aws, new_key):
    job_id, key = new_key()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"first video")
    storage.put_dataset(aws.s3, aws.bucket, job_id, key, {"job_id": job_id, "n": 1}, brain=b"1")
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"second video")
    storage.put_dataset(aws.s3, aws.bucket, job_id, key, {"job_id": job_id, "n": 2}, brain=b"2")
    video = aws.s3.get_object(Bucket=aws.bucket, Key=f"dataset/{job_id}/video.mp4")["Body"]
    assert video.read() == b"second video"
    stored = aws.s3.get_object(Bucket=aws.bucket, Key=f"academic/{job_id}/result.json")["Body"]
    assert json.loads(stored.read())["n"] == 2
