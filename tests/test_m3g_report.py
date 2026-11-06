"""M3g §11: frames, the worker, report billing and web routes (docs/M3g_spec.md).

Covers frame cutting and storage, the worker's frame capture, the report claim/finish/release
functions and the two web routes. The report Lambda and request builder live in
tests/test_m3g_report_lambda.py. Everything is faked: moto S3 and PostgreSQL for billing states;
no Bedrock or network call is made.
"""

import contextlib
import io
import json
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from conftest import STARTER_CENTS, USER_ID, WEB_USER_ID
from neurolens import billing, inference, pricing, results, storage, worker
from neurolens.web.app import create_app

MODULES = ("neurolens.inference", "neurolens.worker")

REPORT = {
    "model": "test-report-model",
    "max_tokens": 4321,
    "max_tries": 2,
    "stale_minutes": 5,
}
MAX_TRIES = REPORT["max_tries"]
STALE_MINUTES = REPORT["stale_minutes"]
REPORT_KEY = "results/{job_id}.report.json"
FRAMES_KEY = "results/{job_id}.frames"


def report_cfg(**overrides):
    return {**REPORT, **overrides}


# ---------------------------------------------------------------- helpers


def job_in_state(db, state="done", *, user_id=USER_ID):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", STARTER_CENTS)
    job_id = str(uuid.uuid4())
    key = f"uploads/{user_id}/{job_id}.mp4"
    billing.reserve(db, user_id, job_id, key, "clip.mp4", 3000)
    if state in ("processing", "done", "failed"):
        assert billing.claim(db, job_id) == 1
    if state == "done":
        assert billing.settle_success(db, job_id) is True
    elif state == "failed":
        assert billing.issue_refund(db, job_id, "stalled", attempt=1) is True
    else:
        assert state in ("queued", "processing")
    return job_id, key


def done_job(db, *, user_id=USER_ID):
    return job_in_state(db, "done", user_id=user_id)


def make_result(duration=3, *, frames=None):
    signals = {
        name: [round(0.02 * (number + 1) + 0.1 * t, 2) for t in range(duration)]
        for number, name in enumerate(results.SERIES)
    }
    muted = {name: [round(value / 2, 2) for value in signals[name]] for name in results.SERIES}
    result = {
        "format": 2,
        "duration_seconds": duration,
        "signals": signals,
        "muted": muted,
    }
    if frames is not None:
        result["frames"] = frames
    return result


def frames_zip(frames, words=()):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for number in reversed(range(len(frames))):
            archive.writestr(f"{number:04d}.jpg", frames[number])
        archive.writestr("words.json", json.dumps(list(words)))
    return buffer.getvalue()


def put_result(aws, job_id, *, frames=3):
    result = make_result(frames=frames)
    assert storage.put_result(aws.s3, aws.bucket, job_id, result) is True
    return result


def put_frames(aws, job_id, frames, words=()):
    storage.put_frames(aws.s3, aws.bucket, job_id, frames_zip(frames, words))


def set_report(pg, job_id, state=None, *, tries=0, error=None, started_minutes_ago=None):
    if started_minutes_ago is None:
        pg.conn.execute(
            "UPDATE jobs SET report_state = %s, report_tries = %s, report_started_at = NULL, "
            "report_error = %s WHERE job_id = %s::uuid",
            (state, tries, error, job_id),
        )
    else:
        pg.conn.execute(
            "UPDATE jobs SET report_state = %s, report_tries = %s, "
            "report_started_at = now() - make_interval(mins => %s), report_error = %s "
            "WHERE job_id = %s::uuid",
            (state, tries, started_minutes_ago, error, job_id),
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
        if name == "put_object":
            keys.append(kwargs.get("Key"))
        elif name == "upload_fileobj":
            keys.append(kwargs.get("Key") or (args[2] if len(args) > 2 else None))
    return keys


# ---------------------------------------------------------------- claim_report / finish / release


def test_claim_report_starts_one_report_and_uses_one_try(db, pg):
    job_id, _ = done_job(db)

    assert billing.claim_report(db, USER_ID, job_id, MAX_TRIES, STALE_MINUTES) is True

    job = pg.job(job_id)
    assert job["report_state"] == "writing"
    assert job["report_tries"] == 1
    assert job["report_started_at"] is not None
    assert job["report_error"] is None


def test_claim_report_cannot_start_a_second_report_while_it_is_writing(db, pg):
    job_id, _ = done_job(db)
    assert billing.claim_report(db, USER_ID, job_id, MAX_TRIES, STALE_MINUTES) is True
    first_start = pg.job(job_id)["report_started_at"]

    assert billing.claim_report(db, USER_ID, job_id, MAX_TRIES, STALE_MINUTES) is False

    job = pg.job(job_id)
    assert (job["report_state"], job["report_tries"]) == ("writing", 1)
    assert job["report_started_at"] == first_start


def test_claim_report_reclaims_a_writing_report_older_than_stale_minutes(db, pg):
    job_id, _ = done_job(db)
    set_report(pg, job_id, "writing", tries=1, error="old", started_minutes_ago=STALE_MINUTES + 1)

    assert billing.claim_report(db, USER_ID, job_id, MAX_TRIES, STALE_MINUTES) is True

    job = pg.job(job_id)
    assert (job["report_state"], job["report_tries"]) == ("writing", 2)
    assert job["report_error"] is None


def test_claim_report_refuses_a_fresh_writing_report(db, pg):
    job_id, _ = done_job(db)
    set_report(pg, job_id, "writing", tries=1, started_minutes_ago=STALE_MINUTES - 1)

    assert billing.claim_report(db, USER_ID, job_id, MAX_TRIES, STALE_MINUTES) is False

    assert pg.job(job_id)["report_tries"] == 1


def test_claim_report_stops_at_max_tries(db, pg):
    job_id, _ = done_job(db)
    set_report(pg, job_id, "failed", tries=MAX_TRIES)

    assert billing.claim_report(db, USER_ID, job_id, MAX_TRIES, STALE_MINUTES) is False

    assert pg.job(job_id)["report_tries"] == MAX_TRIES


def test_claim_report_refuses_another_users_job(db, pg):
    job_id, _ = done_job(db)
    billing.ensure_user(db, "someone-else", "someone-else@example.com", STARTER_CENTS)
    before = pg.job(job_id)

    assert billing.claim_report(db, "someone-else", job_id, MAX_TRIES, STALE_MINUTES) is False

    assert pg.job(job_id) == before


@pytest.mark.parametrize("state", ["queued", "processing", "failed"])
def test_claim_report_refuses_a_job_that_is_not_done(db, pg, state):
    job_id, _ = job_in_state(db, state)
    before = pg.job(job_id)

    assert billing.claim_report(db, USER_ID, job_id, MAX_TRIES, STALE_MINUTES) is False

    assert pg.job(job_id) == before


def test_claim_report_refuses_a_deleted_job(db, pg):
    job_id, _ = done_job(db)
    pg.conn.execute("UPDATE jobs SET deleted_at = now() WHERE job_id = %s::uuid", (job_id,))

    assert billing.claim_report(db, USER_ID, job_id, MAX_TRIES, STALE_MINUTES) is False

    assert pg.job(job_id)["report_state"] is None


def test_claim_report_refuses_a_deleted_user(db, pg):
    job_id, _ = done_job(db)
    pg.conn.execute("UPDATE users SET deleted_at = now() WHERE user_id = %s", (USER_ID,))

    assert billing.claim_report(db, USER_ID, job_id, MAX_TRIES, STALE_MINUTES) is False

    assert pg.job(job_id)["report_state"] is None


def test_finish_report_marks_a_writing_report_ready(db, pg):
    job_id, _ = done_job(db)
    assert billing.claim_report(db, USER_ID, job_id, MAX_TRIES, STALE_MINUTES) is True

    assert billing.finish_report(db, job_id, "ready") is True

    job = pg.job(job_id)
    assert (job["report_state"], job["report_error"]) == ("ready", None)


def test_finish_report_records_the_failure_code(db, pg):
    job_id, _ = done_job(db)
    assert billing.claim_report(db, USER_ID, job_id, MAX_TRIES, STALE_MINUTES) is True

    assert billing.finish_report(db, job_id, "failed", "bad_answer") is True

    job = pg.job(job_id)
    assert (job["report_state"], job["report_error"]) == ("failed", "bad_answer")


def test_finish_report_changes_nothing_unless_the_report_is_writing(db, pg):
    job_id, _ = done_job(db)
    before = pg.job(job_id)

    assert billing.finish_report(db, job_id, "ready") is False

    assert pg.job(job_id) == before


def test_finish_report_does_not_touch_a_deleted_job(db, pg):
    job_id, _ = done_job(db)
    assert billing.claim_report(db, USER_ID, job_id, MAX_TRIES, STALE_MINUTES) is True
    pg.conn.execute("UPDATE jobs SET deleted_at = now() WHERE job_id = %s::uuid", (job_id,))

    assert billing.finish_report(db, job_id, "ready") is False

    job = pg.job(job_id)
    assert (job["report_state"], job["report_error"]) == ("writing", None)


def test_release_report_try_marks_failed_and_gives_the_try_back(db, pg):
    job_id, _ = done_job(db)
    assert billing.claim_report(db, USER_ID, job_id, MAX_TRIES, STALE_MINUTES) is True

    billing.release_report_try(db, job_id)

    job = pg.job(job_id)
    assert (job["report_state"], job["report_tries"]) == ("failed", 0)


# ---------------------------------------------------------------- storage (frames and job keys)


def test_job_keys_include_the_frames_and_report_objects():
    job_id = str(uuid.uuid4())

    keys = storage.job_keys(job_id, f"uploads/{USER_ID}/{job_id}.mp4")

    assert FRAMES_KEY.format(job_id=job_id) in keys
    assert REPORT_KEY.format(job_id=job_id) in keys


def test_get_frames_round_trips_put_frames_in_time_order(aws):
    job_id = str(uuid.uuid4())
    frames = [b"\xff\xd8first", b"\xff\xd8second"]
    words = [{"start": 0.5, "text": "hello"}, {"start": 2.25, "text": "world"}]

    storage.put_frames(aws.s3, aws.bucket, job_id, frames_zip(frames, words))
    got_frames, got_words = storage.get_frames(aws.s3, aws.bucket, job_id)

    assert got_frames == frames
    assert got_words == words


def test_get_frames_raises_on_a_malformed_zip(aws):
    job_id = str(uuid.uuid4())
    aws.s3.put_object(Bucket=aws.bucket, Key=FRAMES_KEY.format(job_id=job_id), Body=b"not a zip")
    get_frames = storage.get_frames

    with pytest.raises(Exception):  # noqa: B017 - the spec names no exception type here
        get_frames(aws.s3, aws.bucket, job_id)


# ---------------------------------------------------------------- inference.make_frames


def jpeg_size(data, tmp_path, name):
    path = tmp_path / name
    path.write_bytes(data)
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    width, height = out.stdout.strip().split(",")
    return int(width), int(height)


def test_make_frames_returns_one_jpeg_per_whole_second(clip_path, tmp_path):
    frames = inference.make_frames(clip_path, 2)

    assert len(frames) == 2
    for number, data in enumerate(frames):
        assert data.startswith(b"\xff\xd8")
        assert jpeg_size(data, tmp_path, f"frame{number}.jpg") == (160, 120)


def test_make_frames_scales_a_large_frame_to_a_640px_long_edge(tmp_path):
    path = tmp_path / "large.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=1280x720:rate=10:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )

    [frame] = inference.make_frames(path, 1)

    width, height = jpeg_size(frame, tmp_path, "large.jpg")
    assert max(width, height) == 640
    assert min(width, height) % 2 == 0


def test_make_frames_raises_on_a_non_video(tmp_path):
    path = tmp_path / "not-a-video.mp4"
    path.write_bytes(b"this is not a video")

    with pytest.raises(inference.FramesFailed):
        inference.make_frames(path, 1)


def test_make_frames_raises_when_there_are_no_frames(clip_path):
    with pytest.raises(inference.FramesFailed):
        inference.make_frames(clip_path, 0)


# ---------------------------------------------------------------- worker frame capture


@pytest.fixture(autouse=True)
def fake_on(monkeypatch):
    monkeypatch.setenv("FAKE_INFERENCE", "1")


@pytest.fixture(autouse=True)
def no_gpu(patch_everywhere):
    patch_everywhere("gpu_info", lambda: None, *MODULES)


def worker_job(db, *, user_id=USER_ID):
    job_id, key = job_in_state(db, "queued", user_id=user_id)
    assert billing.verify_queued(db, job_id, 3000, 120) == "ok"
    return job_id, key


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


class FramesWriteFails:
    def __init__(self, inner):
        self._inner = inner

    def _refuse_frames(self, key):
        if str(key).endswith(".frames"):
            raise ClientError(
                {"Error": {"Code": "InternalError", "Message": "frames write failed"}},
                "PutObject",
            )

    def put_object(self, **kwargs):
        self._refuse_frames(kwargs.get("Key"))
        return self._inner.put_object(**kwargs)

    def upload_fileobj(self, fileobj, bucket, key, **kwargs):
        self._refuse_frames(key)
        return self._inner.upload_fileobj(fileobj, bucket, key, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_the_worker_stores_frames_before_the_result_and_counts_them(
    aws, db, make_cfg, roi_masks_small, clip_path, spy_s3, patch_everywhere
):
    job_id, key = worker_job(db)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    frames = [b"\xff\xd8zero", b"\xff\xd8one", b"\xff\xd8two"]
    patch_everywhere("make_frames", lambda path, seconds: frames, *MODULES)
    s3 = spy_s3()

    assert (
        run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small, s3=s3)
        is worker.Outcome.DONE
    )

    stored = json.loads(object_bytes(aws, f"results/{job_id}.json"))
    assert stored["frames"] == len(frames)
    keys = written_keys(s3)
    assert keys.index(FRAMES_KEY.format(job_id=job_id)) < keys.index(f"results/{job_id}.json")
    with zipfile.ZipFile(io.BytesIO(object_bytes(aws, FRAMES_KEY.format(job_id=job_id)))) as zip_:
        names = sorted(zip_.namelist())
        assert names == ["0000.jpg", "0001.jpg", "0002.jpg", "words.json"]
        assert json.loads(zip_.read("words.json")) == []
        assert all(zip_.getinfo(name).compress_type == zipfile.ZIP_STORED for name in names)


def test_a_frames_failure_still_finishes_and_charges_the_job(
    aws, db, pg, make_cfg, roi_masks_small, clip_path, patch_everywhere
):
    job_id, key = worker_job(db)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)

    def fail(*args, **kwargs):
        raise inference.FramesFailed("no frame")

    patch_everywhere("make_frames", fail, *MODULES)

    assert run_job(aws, db, job_id, clip_path, make_cfg(), roi_masks_small) is worker.Outcome.DONE

    stored = json.loads(object_bytes(aws, f"results/{job_id}.json"))
    assert "frames" not in stored
    assert not object_exists(aws, FRAMES_KEY.format(job_id=job_id))
    assert pg.job(job_id)["status"] == "done"
    assert pg.balance() == (STARTER_CENTS - pricing.estimate_cost_cents(3), 0)


def test_a_frames_s3_failure_still_finishes_and_charges_the_job(
    aws, db, pg, make_cfg, roi_masks_small, clip_path, patch_everywhere
):
    job_id, key = worker_job(db)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)
    patch_everywhere("make_frames", lambda path, seconds: [b"\xff\xd8zero"], *MODULES)

    assert (
        run_job(
            aws, db, job_id, clip_path, make_cfg(), roi_masks_small, s3=FramesWriteFails(aws.s3)
        )
        is worker.Outcome.DONE
    )

    stored = json.loads(object_bytes(aws, f"results/{job_id}.json"))
    assert "frames" not in stored
    assert not object_exists(aws, FRAMES_KEY.format(job_id=job_id))
    assert pg.job(job_id)["status"] == "done"
    assert pg.balance() == (STARTER_CENTS - pricing.estimate_cost_cents(3), 0)


# ---------------------------------------------------------------- web routes


class Starter:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def __call__(self, job_id):
        self.calls.append(job_id)
        if self.error is not None:
            raise self.error


def app_for(aws, db, make_cfg, tmp_path, starter=None):
    cfg = make_cfg(report=report_cfg())
    app = create_app(
        cfg=cfg,
        data_dir=tmp_path / "data",
        db=db,
        s3_client=aws.s3,
        report_starter=starter,
    )
    app.config["TESTING"] = True
    return app.test_client()


def finished_web_job(aws, db, *, with_frames=True):
    job_id, _ = done_job(db, user_id=WEB_USER_ID)
    put_result(aws, job_id, frames=3 if with_frames else None)
    return job_id


def test_post_report_starts_a_report(aws, db, pg, make_cfg, tmp_path):
    starter = Starter()
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    job_id = finished_web_job(aws, db)

    resp = client.post(f"/api/jobs/{job_id}/report")

    assert resp.status_code == 202
    assert resp.get_json()["report_state"] == "writing"
    assert starter.calls == [{"job_id": job_id}]
    job = pg.job(job_id)
    assert (job["report_state"], job["report_tries"]) == ("writing", 1)


def test_post_report_is_404_for_another_users_job(aws, db, make_cfg, tmp_path):
    starter = Starter()
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    job_id, _ = done_job(db, user_id=USER_ID)
    put_result(aws, job_id)

    resp = client.post(f"/api/jobs/{job_id}/report")

    assert resp.status_code == 404
    assert starter.calls == []


@pytest.mark.parametrize("state", ["queued", "processing", "failed"])
def test_post_report_is_404_unless_the_job_is_done(aws, db, make_cfg, tmp_path, state):
    starter = Starter()
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    job_id, _ = job_in_state(db, state, user_id=WEB_USER_ID)

    resp = client.post(f"/api/jobs/{job_id}/report")

    assert resp.status_code == 404
    assert starter.calls == []


def test_post_report_is_409_no_frames(aws, db, make_cfg, tmp_path):
    starter = Starter()
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    job_id = finished_web_job(aws, db, with_frames=False)

    resp = client.post(f"/api/jobs/{job_id}/report")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "no_frames"
    assert starter.calls == []


def test_post_report_is_409_report_ready(aws, db, pg, make_cfg, tmp_path):
    starter = Starter()
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    job_id = finished_web_job(aws, db)
    set_report(pg, job_id, "ready", tries=1)

    resp = client.post(f"/api/jobs/{job_id}/report")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "report_ready"
    assert starter.calls == []


def test_post_report_is_409_report_writing_when_fresh(aws, db, pg, make_cfg, tmp_path):
    starter = Starter()
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    job_id = finished_web_job(aws, db)
    set_report(pg, job_id, "writing", tries=1, started_minutes_ago=0)

    resp = client.post(f"/api/jobs/{job_id}/report")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "report_writing"
    assert starter.calls == []


def test_post_report_is_429_when_no_tries_are_left(aws, db, pg, make_cfg, tmp_path):
    starter = Starter()
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    job_id = finished_web_job(aws, db)
    set_report(pg, job_id, "failed", tries=MAX_TRIES)

    resp = client.post(f"/api/jobs/{job_id}/report")

    assert resp.status_code == 429
    assert resp.get_json()["error"] == "no_tries_left"
    assert starter.calls == []


def test_post_report_retries_a_failed_report_with_tries_left(aws, db, pg, make_cfg, tmp_path):
    starter = Starter()
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    job_id = finished_web_job(aws, db)
    set_report(pg, job_id, "failed", tries=1, error="bad_answer")

    resp = client.post(f"/api/jobs/{job_id}/report")

    assert resp.status_code == 202
    assert starter.calls == [{"job_id": job_id}]
    job = pg.job(job_id)
    assert (job["report_state"], job["report_tries"], job["report_error"]) == ("writing", 2, None)


def test_post_report_start_failure_is_503_and_returns_the_try(aws, db, pg, make_cfg, tmp_path):
    starter = Starter(RuntimeError("lambda unavailable"))
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    job_id = finished_web_job(aws, db)

    resp = client.post(f"/api/jobs/{job_id}/report")

    assert resp.status_code == 503
    job = pg.job(job_id)
    assert (job["report_state"], job["report_tries"]) == ("failed", 0)


def test_get_report_answers_the_state_tries_left_and_no_report(aws, db, pg, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id = finished_web_job(aws, db)
    set_report(pg, job_id, "writing", tries=1, started_minutes_ago=0)

    resp = client.get(f"/api/jobs/{job_id}/report")

    data = resp.get_json()
    assert resp.status_code == 200
    assert data["report_state"] == "writing"
    assert data["tries_left"] == MAX_TRIES - 1
    assert data["report"] is None


def test_get_report_serves_a_ready_report(aws, db, pg, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id = finished_web_job(aws, db)
    report = {"headline": "A headline", "summary": "A summary", "moments": []}
    aws.s3.put_object(
        Bucket=aws.bucket,
        Key=REPORT_KEY.format(job_id=job_id),
        Body=json.dumps(report).encode(),
    )
    set_report(pg, job_id, "ready", tries=1)

    resp = client.get(f"/api/jobs/{job_id}/report")

    data = resp.get_json()
    assert data["report_state"] == "ready"
    assert data["tries_left"] == MAX_TRIES - 1
    assert data["report"] == report


def test_get_report_answers_a_stale_writing_state_as_failed(aws, db, pg, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id = finished_web_job(aws, db)
    set_report(pg, job_id, "writing", tries=1, started_minutes_ago=STALE_MINUTES + 1)

    resp = client.get(f"/api/jobs/{job_id}/report")

    data = resp.get_json()
    assert resp.status_code == 200
    assert data["report_state"] == "failed"
    assert data["report"] is None


def test_get_report_is_404_for_another_users_job(aws, db, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = done_job(db, user_id=USER_ID)
    put_result(aws, job_id)

    resp = client.get(f"/api/jobs/{job_id}/report")

    assert resp.status_code == 404


def test_the_web_app_imports_no_anthropic_or_inference():
    code = """
import json, sys, tempfile
from pathlib import Path
from neurolens.web.app import create_app
create_app(data_dir=Path(tempfile.mkdtemp()))
print(json.dumps([m for m in ("anthropic", "neurolens.inference", "torch") if m in sys.modules]))
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == []
