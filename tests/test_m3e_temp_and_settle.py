"""Publishing a result ends the money decision, and each job gets its own temp folder (M3e §4b,
§11 "Container").

After `put_result` succeeds nothing refunds: a failure to charge is logged as SETTLE_FAILED and
the error handler's `finish_job` settles it. Each invocation runs in a fresh folder under the
base temp directory, with `tempfile.tempdir` and TMPDIR pointing at it; the folder is removed
whatever the outcome, so no video, audio or transcript is left behind.
"""

import contextlib
import logging
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest
from conftest import STARTER_CENTS
from neurolens import billing, settings, storage, worker

JOB_HEADER = "X-Amzn-SageMaker-Custom-Attributes"


@pytest.fixture(autouse=True)
def fake_on(monkeypatch):
    monkeypatch.setenv("FAKE_INFERENCE", "1")


def no_keep_alive(on_beat):
    return contextlib.nullcontext()


def verify(db, job_id, duration_ms=3000):
    assert billing.verify_queued(db, job_id, duration_ms, settings.max_duration(None)) == "ok"


def upload(aws, key, clip_path):
    aws.s3.upload_file(str(clip_path), aws.bucket, key)


def run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small):
    return worker.handle_job(
        job_id,
        str(clip_path),
        s3=aws.s3,
        db=db,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        keep_alive=no_keep_alive,
    )


def object_exists(aws, key):
    return storage.object_exists(aws.s3, aws.bucket, key)


# ---------------------------------------------------------------- settlement after publish


def test_settle_that_raises_before_committing_keeps_files_and_finish_job_charges_once(
    aws,
    db,
    pg,
    make_cfg,
    roi_masks_small,
    new_job,
    clip_path,
    patch_everywhere,
    monkeypatch,
    caplog,
):
    from neurolens.lambdas import finish_job

    job_id, key = new_job()
    upload(aws, key, clip_path)
    verify(db, job_id)
    real_settle = billing.settle_success

    def settle_then_raise(db, job_id):
        raise RuntimeError("the database went away")

    patch_everywhere("settle_success", settle_then_raise, "neurolens.billing", "neurolens.worker")

    with caplog.at_level(logging.ERROR):
        outcome = run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small)

    assert outcome is worker.Outcome.FAILED
    job = pg.job(job_id)
    assert job["status"] == "processing" and job["captured_cents"] is None
    assert pg.balance() == (STARTER_CENTS - 90, 90)
    assert "refund" not in pg.kinds()
    for kept in (
        key,
        f"results/{job_id}.json",
        f"results/{job_id}.brain",
        f"results/{job_id}.jpg",
    ):
        assert object_exists(aws, kept)
    assert f"SETTLE_FAILED {job_id}" in caplog.text

    monkeypatch.setattr(billing, "settle_success", real_settle)
    assert finish_job(db, aws.s3, aws.bucket, job_id) is True

    job = pg.job(job_id)
    assert job["status"] == "done" and job["captured_cents"] == 90
    assert pg.balance() == (STARTER_CENTS - 90, 0)
    assert pg.kinds().count("capture") == 1 and "refund" not in pg.kinds()


# ---------------------------------------------------------------- the per-job temp folder


def http(server, method, path, *, body=None, headers=None, timeout=5):
    host, port = server.server_address[:2]
    request = urllib.request.Request(
        f"http://{host}:{port}{path}", data=body, method=method, headers=headers or {}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


@pytest.fixture
def temp_base(tmp_path, monkeypatch):
    base = tmp_path / "tmp"
    base.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(base))
    monkeypatch.setenv("TMPDIR", str(base))
    return base


@pytest.mark.parametrize(
    ("outcome_name", "status"),
    [("DONE", 200), ("FAILED", 500), ("REJECTED", 200)],
)
def test_the_job_temp_folder_is_removed_and_tmp_is_restored(
    aws, db, make_cfg, roi_masks_small, monkeypatch, temp_base, outcome_name, status
):
    cache = temp_base / "features"
    cache.mkdir()
    marker = cache / "keep-me"
    marker.write_text("the feature cache survives")
    instance = worker.make_server(
        "127.0.0.1",
        0,
        s3=aws.s3,
        db=db,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        feature_cache=str(cache),
    )
    state = {}

    def fake(job_id, video_path, *, s3, db, cfg, roi_masks, keep_alive=None):
        folder = Path(video_path).parent
        state["folder"] = folder
        assert folder.parent == temp_base
        assert folder != cache
        assert Path(tempfile.gettempdir()) == folder
        assert os.environ["TMPDIR"] == str(folder)
        assert Path(video_path).read_bytes() == b"video bytes"
        for suffix in (".wav", ".tsv"):
            handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            handle.write(b"temporary")
            handle.close()
            assert Path(handle.name).parent == folder
        return getattr(worker.Outcome, outcome_name)

    monkeypatch.setattr(worker, "handle_job", fake)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        code, _ = http(
            instance,
            "POST",
            "/invocations",
            body=b"video bytes",
            headers={JOB_HEADER: str(uuid.uuid4())},
        )
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)

    assert code == status
    assert not state["folder"].exists()
    assert tempfile.tempdir == str(temp_base)
    assert os.environ["TMPDIR"] == str(temp_base)
    assert marker.read_text() == "the feature cache survives"
    assert sorted(path.name for path in temp_base.iterdir()) == ["features"]


# ---------------------------------------------------------------- keep-alive


def test_a_beat_that_reports_a_lost_claim_stops_the_keep_alive(caplog):
    beats = []

    def beat():
        beats.append(time.monotonic())
        return len(beats) < 2  # True, then False: the claim is gone

    with caplog.at_level(logging.WARNING):
        with worker.ClaimKeepAlive(beat, 0.02, 5):
            time.sleep(0.2)

    count = len(beats)
    time.sleep(0.1)
    assert count >= 2
    assert len(beats) == count
    assert "claim" in caplog.text.lower()


def test_a_lost_claim_still_overruns_once_at_the_deadline(caplog):
    beats = []
    overruns = []
    started = time.monotonic()

    def beat():
        beats.append(time.monotonic())
        return False  # the claim is gone on the first beat

    with caplog.at_level(logging.WARNING):
        with worker.ClaimKeepAlive(beat, 0.02, 0.15, lambda: overruns.append(time.monotonic())):
            time.sleep(0.35)

    count = len(beats)
    time.sleep(0.1)
    assert count == 1
    assert len(beats) == count
    assert len(overruns) == 1
    assert overruns[0] - started >= 0.15
    assert "claim" in caplog.text.lower()
