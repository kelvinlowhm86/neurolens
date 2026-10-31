"""Step 5b: every upload gets a phone-friendly H.264 play copy.

Some uploads (VP9, HEVC, huge files) do not play in phone browsers. The worker makes an H.264
copy at `results/<job_id>.play.mp4`, the page plays it, and every deletion path removes it.

These tests are written before the feature. The missing names are looked up at call time
(`inference.PlayCopy`, `storage.put_play_copy`, the `.play.mp4` key), so collection succeeds and
each missing piece fails on its own.
"""

import contextlib
import json
import logging
import subprocess
import uuid
from urllib.parse import unquote, urlparse

import pytest
from botocore.exceptions import ClientError
from conftest import STARTER_CENTS, USER_ID, WEB_USER_ID
from neurolens import billing, inference, pricing, settings, storage, worker
from neurolens.web.app import create_app

MODULES = ("neurolens.inference", "neurolens.worker")
PLAY_KEY = "results/{job_id}.play.mp4"


@pytest.fixture(autouse=True)
def fake_on(monkeypatch):
    monkeypatch.setenv("FAKE_INFERENCE", "1")


@pytest.fixture(autouse=True)
def no_gpu(patch_everywhere):
    patch_everywhere("gpu_info", lambda: None, *MODULES)


# ---------------------------------------------------------------- shared helpers


def make_job(db, *, consent=False):
    job_id = str(uuid.uuid4())
    key = f"uploads/{USER_ID}/{job_id}.mp4"
    billing.ensure_user(db, USER_ID, f"{USER_ID}@example.com", STARTER_CENTS)
    billing.reserve(db, USER_ID, job_id, key, "clip.mp4", 3000, research_consent=consent)
    return job_id, key


def verify(db, job_id, duration_ms=3000):
    assert billing.verify_queued(db, job_id, duration_ms, settings.max_duration(None)) == "ok"


def run_job(aws, db, job_id, video_path, cfg, masks, *, s3=None):
    return worker.handle_job(
        job_id,
        str(video_path),
        s3=s3 or aws.s3,
        db=db,
        cfg=cfg,
        roi_masks=masks,
        keep_alive=lambda on_beat: contextlib.nullcontext(),
    )


def object_exists(aws, key):
    return storage.object_exists(aws.s3, aws.bucket, key)


def object_bytes(aws, key):
    return aws.s3.get_object(Bucket=aws.bucket, Key=key)["Body"].read()


def call_key(name, args, kwargs):
    if name in ("put_object", "copy_object"):
        return kwargs.get("Key") or (args[1] if len(args) > 1 else None)
    if name in ("upload_file", "upload_fileobj"):
        return kwargs.get("Key") or (args[2] if len(args) > 2 else None)
    return None


def written_keys(s3):
    keys = []
    for name, args, kwargs in s3.calls:
        if name in ("put_object", "copy_object", "upload_file", "upload_fileobj"):
            key = call_key(name, args, kwargs)
            if key:
                keys.append(key)
    return keys


def assert_charged(pg, job_id, seconds=3):
    price = pricing.estimate_cost_cents(seconds)
    job = pg.job(job_id)
    assert job["status"] == "done" and job["captured_cents"] == price
    assert pg.balance() == (STARTER_CENTS - price, 0)


def probe_streams(aws, key, tmp_path):
    path = tmp_path / "probe.mp4"
    path.write_bytes(object_bytes(aws, key))
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["streams"]


def probe_size(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    stream = json.loads(result.stdout)["streams"][0]
    return stream["width"], stream["height"]


def make_video(path, size, *, seconds=1, pix_fmt="yuv420p"):
    width, height = size
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={width}x{height}:rate=10:duration={seconds}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            pix_fmt,
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture(scope="module")
def long_clip(tmp_path_factory):
    """A long enough encode that a zero timeout or an exception still finds it running."""
    path = tmp_path_factory.mktemp("long_clip") / "long.mp4"
    return make_video(path, (640, 480), seconds=60)


class PlayUploadFails:
    """A real S3 client whose copy upload to the `.play.mp4` key raises, as S3 may."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def wrapper(*args, **kwargs):
            key = call_key(name, args, kwargs)
            if key is not None and str(key).endswith(".play.mp4"):
                raise ClientError(
                    {"Error": {"Code": "InternalError", "Message": "play upload failed"}},
                    "PutObject",
                )
            return attr(*args, **kwargs)

        return wrapper


# ---------------------------------------------------------------- the key and the worker


def test_job_keys_include_the_play_copy():
    job_id = str(uuid.uuid4())

    keys = storage.job_keys(job_id, f"uploads/{USER_ID}/{job_id}.mp4")

    assert PLAY_KEY.format(job_id=job_id) in keys


def test_a_worker_run_writes_a_phone_playable_copy_before_the_result(
    aws, db, make_cfg, roi_masks_small, clip_path, spy_s3, tmp_path
):
    job_id, key = make_job(db)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)
    s3 = spy_s3()

    assert (
        run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small, s3=s3)
        is worker.Outcome.DONE
    )

    play_key = PLAY_KEY.format(job_id=job_id)
    assert object_exists(aws, play_key)
    assert aws.s3.head_object(Bucket=aws.bucket, Key=play_key)["ContentType"] == "video/mp4"
    streams = probe_streams(aws, play_key, tmp_path)
    video = [stream for stream in streams if stream["codec_type"] == "video"]
    audio = [stream for stream in streams if stream["codec_type"] == "audio"]
    assert video and video[0]["codec_name"] == "h264"
    assert video[0]["pix_fmt"] == "yuv420p"
    assert audio, "clip_path has an audio track, so the copy must keep one"

    keys = written_keys(s3)
    assert keys.index(play_key) < keys.index(f"results/{job_id}.json")


def test_a_video_without_audio_gets_a_playable_copy_and_the_job_is_done(
    aws, db, pg, make_cfg, roi_masks_small, clip2_path, tmp_path
):
    job_id, key = make_job(db)
    aws.s3.upload_file(str(clip2_path), aws.bucket, key)
    verify(db, job_id)

    assert run_job(aws, db, job_id, clip2_path, make_cfg(), roi_masks_small) is worker.Outcome.DONE

    play_key = PLAY_KEY.format(job_id=job_id)
    streams = probe_streams(aws, play_key, tmp_path)
    video = [stream for stream in streams if stream["codec_type"] == "video"]
    audio = [stream for stream in streams if stream["codec_type"] == "audio"]
    assert video and video[0]["codec_name"] == "h264"
    assert not audio
    assert pg.job(job_id)["status"] == "done"


# ---------------------------------------------------------------- PlayCopy itself


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((2400, 1350), (1920, 1080)),
        ((640, 360), (640, 360)),
        ((1081, 1921), None),
    ],
    ids=["landscape_capped", "small_unchanged", "portrait_even"],
)
def test_play_copy_caps_the_long_edge_and_keeps_both_sides_even(tmp_path, size, expected):
    source = make_video(
        tmp_path / "source.mp4", size, pix_fmt="yuv444p" if any(d % 2 for d in size) else "yuv420p"
    )
    destination = tmp_path / "play.mp4"

    with inference.PlayCopy(source, destination) as copy:
        assert copy.wait(timeout_s=inference.PLAY_COPY_WAIT_SECONDS) is True

    width, height = probe_size(destination)
    if expected is not None:
        assert (width, height) == expected
    else:
        assert max(width, height) <= 1920
        assert width > height
        assert width % 2 == 0 and height % 2 == 0


def test_wait_with_no_time_left_gives_up_and_reaps_the_process(tmp_path, long_clip):
    destination = tmp_path / "play.mp4"

    with inference.PlayCopy(long_clip, destination) as copy:
        assert copy.wait(timeout_s=0) is False
        assert copy.process.poll() is not None


def test_an_exception_leaves_no_running_encode(tmp_path, long_clip):
    destination = tmp_path / "play.mp4"
    process = None

    with pytest.raises(RuntimeError):
        with inference.PlayCopy(long_clip, destination) as copy:
            process = copy.process
            raise RuntimeError("analysis failed")

    assert process is not None
    assert process.poll() is not None


# ---------------------------------------------------------------- failures never fail the job


def test_a_failed_play_encode_still_charges_the_job_and_logs(
    aws, db, pg, make_cfg, roi_masks_small, clip_path, monkeypatch, caplog
):
    job_id, key = make_job(db)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)
    monkeypatch.setattr(inference.PlayCopy, "wait", lambda self, timeout_s=None: False)

    with caplog.at_level(logging.ERROR, logger="neurolens"):
        outcome = run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small)

    assert outcome is worker.Outcome.DONE
    assert_charged(pg, job_id)
    assert not object_exists(aws, PLAY_KEY.format(job_id=job_id))
    assert "PLAY_COPY_FAILED" in caplog.text


def test_a_failed_play_upload_still_charges_the_job_and_logs(
    aws, db, pg, make_cfg, roi_masks_small, clip_path, caplog
):
    job_id, key = make_job(db)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)

    with caplog.at_level(logging.ERROR, logger="neurolens"):
        outcome = run_job(
            aws,
            db,
            job_id,
            clip_path,
            make_cfg(),
            roi_masks_small,
            s3=PlayUploadFails(aws.s3),
        )

    assert outcome is worker.Outcome.DONE
    assert_charged(pg, job_id)
    assert not object_exists(aws, PLAY_KEY.format(job_id=job_id))
    assert "PLAY_COPY_FAILED" in caplog.text


def test_an_inference_failure_refunds_and_leaves_no_encode_behind(
    aws, db, pg, make_cfg, roi_masks_small, clip_path, monkeypatch, patch_everywhere
):
    job_id, key = make_job(db)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id)
    recorded = []
    real_popen = subprocess.Popen

    def recording_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        recorded.append(process)
        return process

    monkeypatch.setattr(inference.subprocess, "Popen", recording_popen)

    def failing(*args, **kwargs):
        raise RuntimeError("model failed")

    patch_everywhere("predict", failing, *MODULES)

    assert run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small) is worker.Outcome.FAILED

    assert pg.job(job_id)["status"] == "failed"
    assert pg.balance() == (STARTER_CENTS, 0)
    assert not object_exists(aws, PLAY_KEY.format(job_id=job_id))
    encodes = [process for process in recorded if "libx264" in str(process.args)]
    assert encodes, "the worker starts the play encode before inference"
    assert all(process.poll() is not None for process in encodes)


def test_a_rejected_video_never_creates_a_play_copy(
    aws, db, pg, make_cfg, roi_masks_small, clip_path, monkeypatch
):
    job_id, key = make_job(db)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    verify(db, job_id, duration_ms=1000)  # the 3-second clip is over the checked length + 1 s
    recorded = []
    real_popen = subprocess.Popen

    def recording_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        recorded.append(process)
        return process

    monkeypatch.setattr(inference.subprocess, "Popen", recording_popen)

    assert (
        run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small) is worker.Outcome.REJECTED
    )

    assert pg.job(job_id)["error_code"] == "video_changed"
    assert not object_exists(aws, PLAY_KEY.format(job_id=job_id))
    assert not [process for process in recorded if "libx264" in str(process.args)]


# ---------------------------------------------------------------- the video route


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


def done_web_job(db, *, user_id=WEB_USER_ID, content_type="video/mp4", consent=False):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", STARTER_CENTS)
    job_id = str(uuid.uuid4())
    key = storage.object_key(user_id, job_id, content_type)
    billing.reserve(db, user_id, job_id, key, "clip.mp4", 27_400, research_consent=consent)
    assert billing.claim(db, job_id) == 1
    assert billing.settle_success(db, job_id) is True
    return job_id, key


def store_result(aws, job_id):
    result = {"format": 2, "duration_seconds": 0, "signals": {}, "muted": {}, "job_id": job_id}
    assert storage.put_result(aws.s3, aws.bucket, job_id, result) is True


def assert_presigns(response, key):
    assert response.status_code == 200
    data = response.get_json()
    assert data["expires_in"] == 3600
    assert unquote(urlparse(data["url"]).path).endswith(key)


def test_the_video_route_prefers_the_play_copy(client, aws, db):
    job_id, key = done_web_job(db)
    store_result(aws, job_id)
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"the upload")
    play_key = PLAY_KEY.format(job_id=job_id)
    aws.s3.put_object(Bucket=aws.bucket, Key=play_key, Body=b"the play copy")

    assert_presigns(client.get(f"/api/jobs/{job_id}/video"), play_key)


def test_the_video_route_falls_back_to_the_upload(client, aws, db):
    job_id, key = done_web_job(db)
    store_result(aws, job_id)
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"the upload")

    assert_presigns(client.get(f"/api/jobs/{job_id}/video"), key)


def test_the_video_route_falls_back_to_a_consented_dataset_copy(client, aws, db):
    job_id, _ = done_web_job(db, content_type="video/quicktime", consent=True)
    store_result(aws, job_id)
    dataset_key = f"dataset/{job_id}/video.mov"
    aws.s3.put_object(Bucket=aws.bucket, Key=dataset_key, Body=b"the dataset copy")

    assert_presigns(client.get(f"/api/jobs/{job_id}/video"), dataset_key)


def test_the_video_route_is_404_video_deleted_with_nothing_kept(client, aws, db):
    job_id, _ = done_web_job(db)
    store_result(aws, job_id)

    response = client.get(f"/api/jobs/{job_id}/video")

    assert response.status_code == 404
    assert response.get_json()["error"] == "video_deleted"


def test_the_video_route_does_not_serve_another_users_job(client, aws, db):
    job_id, key = done_web_job(db, user_id="someone-else")
    store_result(aws, job_id)
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"the upload")
    play_key = PLAY_KEY.format(job_id=job_id)
    aws.s3.put_object(Bucket=aws.bucket, Key=play_key, Body=b"the play copy")

    response = client.get(f"/api/jobs/{job_id}/video")

    assert response.status_code == 404
    assert response.get_json()["error"] == "not_found"


# ---------------------------------------------------------------- deletions


def test_purging_a_deleted_job_removes_the_play_copy(aws, db):
    job_id, key = done_web_job(db)
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"the upload")
    play_key = PLAY_KEY.format(job_id=job_id)
    aws.s3.put_object(Bucket=aws.bucket, Key=play_key, Body=b"the play copy")

    assert billing.delete_job(db, WEB_USER_ID, job_id) is True
    billing.purge_job_files(db, aws.s3, aws.bucket, job_id)

    assert not object_exists(aws, play_key)


@pytest.fixture
def reaper_env(aws, db, monkeypatch, tmp_path, patch_everywhere):
    """The reaper's Lambda environment, built as tests/test_m3e_reaper.py does."""
    monkeypatch.setenv(
        "NEUROLENS_DB_CLUSTER_ARN", "arn:aws:rds:us-east-1:000000000000:cluster:neurolens-db"
    )
    monkeypatch.setenv(
        "NEUROLENS_DB_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:000000000000:secret:x"
    )
    monkeypatch.setenv("NEUROLENS_DB_NAME", "neurolens")
    monkeypatch.setenv("NEUROLENS_S3_BUCKET", aws.bucket)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    (root / "config.json").write_text(json.dumps({"research": {"no_curve_days": 90}}))
    monkeypatch.setenv("NEUROLENS_ROOT", str(root))

    def data_api_database(*args, **kwargs):
        return db

    patch_everywhere(
        "DataApiDatabase", data_api_database, "neurolens.db", "neurolens.lambdas.reaper"
    )


def test_the_reaper_removes_the_play_copy_of_a_stale_job(aws, db, pg, reaper_env, new_job):
    from neurolens.lambdas import reaper

    job_id, key = new_job()
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"the upload")
    play_key = PLAY_KEY.format(job_id=job_id)
    aws.s3.put_object(Bucket=aws.bucket, Key=play_key, Body=b"the play copy")
    pg.age(job_id, updated_s=5 * 3600, created_s=5 * 3600)

    reaper.handler({}, None)

    assert pg.job(job_id)["error_code"] == "deadline_passed"
    assert not object_exists(aws, play_key)
