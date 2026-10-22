"""M3d §5b and §7: thumbnails, their lifecycle, and the thumbnail route.

Written first from docs/M3d_spec.md §10 ("Thumbnail (full: deletion and ownership)").
The worker runs in fake mode as in tests/test_m3d_worker.py; deletes use moto.
"""

import contextlib
import subprocess
import uuid
from pathlib import PurePosixPath
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from conftest import STARTER_CENTS, USER_ID, WEB_USER_ID, sign_in
from neurolens import billing, inference, response, settings, storage, worker
from neurolens.web.app import create_app

MODULES = ("neurolens.inference", "neurolens.worker")


@pytest.fixture(autouse=True)
def fake_on(monkeypatch):
    monkeypatch.setenv("FAKE_INFERENCE", "1")


@pytest.fixture(autouse=True)
def no_gpu(patch_everywhere):
    patch_everywhere("gpu_info", lambda: None, *MODULES)


def make_job(db, *, consent=False, user_id=USER_ID):
    job_id = str(uuid.uuid4())
    key = f"uploads/{user_id}/{job_id}.mp4"
    billing.ensure_user(db, user_id, f"{user_id}@example.com", STARTER_CENTS)
    billing.reserve(
        db,
        user_id,
        job_id,
        key,
        "clip.mp4",
        3000,
        research_consent=consent,
    )
    return job_id, key


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
        keep_alive=lambda on_beat: contextlib.nullcontext(),
    )


def object_exists(aws, key):
    try:
        aws.s3.head_object(Bucket=aws.bucket, Key=key)
    except ClientError as err:
        assert err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound")
        return False
    return True


# ---------------------------------------------------------------- inference.make_thumbnail


def jpeg_width(path):
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width",
            "-of",
            "csv=p=0",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(out.stdout.strip())


def test_make_thumbnail_returns_a_320px_jpeg(clip_path, tmp_path):
    data = inference.make_thumbnail(clip_path, 1.5)

    assert data.startswith(b"\xff\xd8")
    path = tmp_path / "thumb.jpg"
    path.write_bytes(data)
    assert jpeg_width(path) == 320


def test_make_thumbnail_raises_on_a_non_video(tmp_path):
    path = tmp_path / "not-a-video.mp4"
    path.write_bytes(b"this is not a video")

    with pytest.raises(inference.ThumbnailFailed):
        inference.make_thumbnail(path, 0)


# ---------------------------------------------------------------- the worker writes it


def test_a_done_job_writes_a_jpeg_thumbnail(aws, db, make_cfg, roi_masks_small, clip_path):
    job_id, key = make_job(db)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)

    assert run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small) is worker.Outcome.DONE

    obj = aws.s3.get_object(Bucket=aws.bucket, Key=f"results/{job_id}.jpg")
    assert obj["ContentType"] == "image/jpeg"
    assert obj["Body"].read().startswith(b"\xff\xd8")


def dataset_keys(aws, job_id):
    resp = aws.s3.list_objects_v2(Bucket=aws.bucket, Prefix=f"dataset/{job_id}/")
    return [obj["Key"] for obj in resp.get("Contents", [])]


def test_a_consented_job_writes_no_thumbnail_into_the_dataset(
    aws, db, make_cfg, roi_masks_small, clip_path
):
    job_id, key = make_job(db, consent=True)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)

    assert run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small) is worker.Outcome.DONE

    assert object_exists(aws, f"results/{job_id}.jpg")
    assert not any(name.endswith(".jpg") for name in dataset_keys(aws, job_id))


def test_a_job_refunded_before_saving_result_gets_no_thumbnail(
    aws, db, make_cfg, roi_masks_small, clip_path, patch_everywhere
):
    done_id, done_key = make_job(db)
    aws.s3.upload_file(str(clip_path), aws.bucket, done_key)
    verify(db, done_id)
    assert run_job(aws, db, done_id, clip_path, make_cfg(), roi_masks_small) is worker.Outcome.DONE
    assert object_exists(aws, f"results/{done_id}.jpg")  # the control: a finished job has one

    job_id, key = make_job(db)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)
    real_extract = response.extract_response

    def extract_then_refund(*args, **kwargs):
        result = real_extract(*args, **kwargs)
        assert billing.issue_refund(db, job_id, "stalled", attempt=1) is True
        return result

    patch_everywhere(
        "extract_response", extract_then_refund, "neurolens.response", "neurolens.worker"
    )

    assert (
        run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small)
        is worker.Outcome.LOST_CLAIM
    )
    assert not object_exists(aws, f"results/{job_id}.jpg")


def test_a_failed_thumbnail_does_not_fail_the_job(
    aws, db, pg, make_cfg, roi_masks_small, clip_path, patch_everywhere
):
    job_id, key = make_job(db)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)
    failure = inference.ThumbnailFailed

    def fail(*args, **kwargs):
        raise failure("no frame")

    patch_everywhere("make_thumbnail", fail, *MODULES)

    assert run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small) is worker.Outcome.DONE

    assert not object_exists(aws, f"results/{job_id}.jpg")
    assert pg.job(job_id)["status"] == "done"
    assert pg.balance() == (STARTER_CENTS - 90, 0)


# ---------------------------------------------------------------- deletion


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


class NoCallS3:
    """A stubbed S3 client that records (and refuses) every call."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"s3.{name} must not be called here")

        return call


def finished_job(db, *, user_id=WEB_USER_ID, state="done"):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", STARTER_CENTS)
    job_id = str(uuid.uuid4())
    key = storage.object_key(user_id, job_id, "video/mp4")
    billing.reserve(db, user_id, job_id, key, "clip.mp4", 3000)
    if state == "processing":
        assert billing.claim(db, job_id) == 1
    elif state == "failed":
        assert billing.claim(db, job_id) == 1
        assert billing.issue_refund(db, job_id, "stalled", attempt=1) is True
    elif state == "done":
        assert billing.claim(db, job_id) == 1
        assert billing.settle_success(db, job_id) is True
    else:
        assert state == "queued"
    return job_id, key


def seed_files(aws, job_id, key):
    ext = PurePosixPath(key).suffix
    keys = [
        key,
        f"results/{job_id}.json",
        f"results/{job_id}.brain",
        f"results/{job_id}.jpg",
        f"dataset/{job_id}/video{ext}",
        f"dataset/{job_id}/result.json",
        f"dataset/{job_id}/brain",
        f"dataset/{job_id}/retention.csv",
    ]
    for name in keys:
        aws.s3.put_object(Bucket=aws.bucket, Key=name, Body=b"x")
    return keys


def test_job_keys_includes_the_thumbnail():
    job_id = str(uuid.uuid4())

    keys = storage.job_keys(job_id, f"uploads/{WEB_USER_ID}/{job_id}.mp4")

    assert f"results/{job_id}.jpg" in keys


def test_per_video_delete_removes_the_thumbnail(client, aws, db):
    job_id, key = finished_job(db)
    seed_files(aws, job_id, key)

    resp = client.delete(f"/api/jobs/{job_id}")

    assert resp.status_code in (200, 204)
    assert not object_exists(aws, f"results/{job_id}.jpg")


def test_cancel_removes_the_thumbnail(client, aws, db):
    job_id, key = finished_job(db, state="queued")
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"video")
    aws.s3.put_object(Bucket=aws.bucket, Key=f"results/{job_id}.jpg", Body=b"jpeg")

    resp = client.post(f"/api/jobs/{job_id}/cancel", json={})

    assert resp.status_code == 200
    assert not object_exists(aws, f"results/{job_id}.jpg")


@pytest.fixture
def cognito_admin(monkeypatch):
    """Enough of Cognito's admin API for account deletion: no users to delete."""

    real = BaseClient._make_api_call

    def make_api_call(self, operation_name, api_params):
        if operation_name == "ListUsers":
            return {"Users": []}
        if operation_name == "AdminDeleteUser":
            return {}
        return real(self, operation_name, api_params)

    monkeypatch.setattr(BaseClient, "_make_api_call", make_api_call)


def test_account_delete_removes_the_thumbnail(cognito_client, idp, aws, db, pg, cognito_admin):
    assert sign_in(cognito_client, idp, email="ann@example.com").status_code in (302, 303)
    user_id = pg.one("SELECT user_id FROM users WHERE lower(email) = 'ann@example.com'")["user_id"]
    job_id, key = finished_job(db, user_id=user_id)
    seed_files(aws, job_id, key)

    resp = cognito_client.post("/api/account/delete", json={"confirm": "DELETE"})

    assert resp.status_code == 200
    assert not object_exists(aws, f"results/{job_id}.jpg")


# ---------------------------------------------------------------- the route


def test_the_thumbnail_route_owns_the_job_before_any_s3_call(app_for, aws, db):
    job_id, _ = finished_job(db, user_id="someone-else")
    s3 = NoCallS3()

    resp = app_for(s3_client=s3).get(f"/api/jobs/{job_id}/thumbnail")

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"
    assert s3.calls == []


@pytest.mark.parametrize("state", ["queued", "processing", "failed"])
def test_a_job_that_is_not_done_is_404_thumbnail_missing(client, aws, db, state):
    job_id, _ = finished_job(db, state=state)

    resp = client.get(f"/api/jobs/{job_id}/thumbnail")

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "thumbnail_missing"


def test_the_thumbnail_route_redirects_to_a_cached_presigned_url(client, aws, db):
    job_id, _ = finished_job(db)
    # The object deliberately does not exist: the route must not check.

    resp = client.get(f"/api/jobs/{job_id}/thumbnail")

    assert resp.status_code == 302
    parsed = urlparse(resp.headers["Location"])
    assert unquote(parsed.path).endswith(f"results/{job_id}.jpg")
    query = parse_qs(parsed.query)
    assert query["X-Amz-Signature"]
    assert query["X-Amz-Expires"] == ["300"]
    assert resp.headers["Cache-Control"] == "private, max-age=300"
