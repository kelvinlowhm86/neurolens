"""M3e GPU container (docs/M3e_spec.md §4a, §4b, §4c, §11 "Container").

SageMaker has already downloaded the upload and POSTs it to the container, so `handle_job` gets
a local video path and takes the object key and verified duration from the job row. These tests
use fake inference (`FAKE_INFERENCE=1`), a real 3-second clip and moto S3, because the dataset
copy and the refund deletes read the bucket.

Each test checks one outcome with a product meaning: money charged or returned, files kept or
deleted, a job left alone, the request answered with the right status, or the keep-alive stopping
before SageMaker's 60-minute timeout.
"""

import contextlib
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import STARTER_CENTS, USER_ID
from neurolens import billing, inference, settings, storage, worker

JOB_HEADER = "X-Amzn-SageMaker-Custom-Attributes"
STAGES = [
    "transcribing",
    "inference_full",
    "inference_noaudio",
    "extracting_roi",
    "saving_result",
]


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


def consented_job(db, new_key):
    job_id, key = new_key()
    billing.ensure_user(db, USER_ID, f"{USER_ID}@example.com", STARTER_CENTS)
    billing.reserve(db, USER_ID, job_id, key, "clip.mp4", 3000, research_consent=True)
    return job_id, key


def assert_refunded(pg, job_id, reason, balance=(STARTER_CENTS, 0)):
    job = pg.job(job_id)
    assert job["status"] == "failed"
    assert job["error_code"] == reason
    assert pg.refund_reason(job_id) == reason
    assert pg.balance() == balance


def object_exists(aws, key):
    return storage.object_exists(aws.s3, aws.bucket, key)


# ---------------------------------------------------------------- outcomes and success


def test_outcome_has_no_busy_member():
    assert {member.name for member in worker.Outcome} == {
        "DONE",
        "DUPLICATE",
        "REJECTED",
        "SKIPPED",
        "FAILED",
        "LOST_CLAIM",
    }


def test_success_charges_once_and_keeps_the_result_files(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path
):
    job_id, key = new_job()
    upload(aws, key, clip_path)
    verify(db, job_id)

    outcome = run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small)

    assert outcome is worker.Outcome.DONE
    job = pg.job(job_id)
    assert job["status"] == "done"
    assert job["captured_cents"] == 90 and job["attempt"] == 1
    assert pg.balance() == (410, 0)
    assert pg.kinds() == ["starter", "reserve", "capture"]
    assert [stage["stage"] for stage in job["stages"]] == STAGES
    result = json.loads(
        aws.s3.get_object(Bucket=aws.bucket, Key=f"results/{job_id}.json")["Body"].read()
    )
    assert result["job_id"] == job_id
    for kept in (
        key,
        f"results/{job_id}.json",
        f"results/{job_id}.brain",
        f"results/{job_id}.jpg",
    ):
        assert object_exists(aws, kept)


def test_a_consented_job_keeps_its_dataset_copy(
    aws, db, pg, make_cfg, roi_masks_small, new_key, clip_path
):
    job_id, key = consented_job(db, new_key)
    upload(aws, key, clip_path)
    verify(db, job_id)

    assert run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small) is worker.Outcome.DONE

    assert object_exists(aws, f"dataset/{job_id}/video.mp4")
    for kept in (
        f"academic/{job_id}/result.json",
        f"academic/{job_id}/brain",
    ):
        assert object_exists(aws, kept)
    result = json.loads(
        aws.s3.get_object(Bucket=aws.bucket, Key=f"results/{job_id}.json")["Body"].read()
    )
    assert result["job_id"] == job_id


def test_an_existing_result_settles_the_job_and_skips_the_analysis(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path
):
    job_id, key = new_job()
    upload(aws, key, clip_path)
    theirs = {"job_id": job_id, "marker": "the earlier worker"}
    assert storage.put_result(aws.s3, aws.bucket, job_id, theirs) is True

    assert run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small) is worker.Outcome.SKIPPED

    job = pg.job(job_id)
    assert job["status"] == "done" and job["captured_cents"] == 90
    assert pg.balance() == (410, 0)
    stored = json.loads(
        aws.s3.get_object(Bucket=aws.bucket, Key=f"results/{job_id}.json")["Body"].read()
    )
    assert stored == theirs


def test_an_unknown_job_is_skipped_without_touching_the_upload(
    aws, db, pg, make_cfg, roi_masks_small, new_key, clip_path
):
    _, key = new_key()
    upload(aws, key, clip_path)
    job_id = str(uuid.uuid4())

    assert run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small) is worker.Outcome.SKIPPED

    assert pg.job(job_id) is None
    assert object_exists(aws, key)


# ---------------------------------------------------------------- claim refusals


@pytest.mark.parametrize("terminal", ["failed", "done"])
def test_a_terminal_job_is_skipped_and_a_failed_one_loses_its_upload(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, terminal
):
    job_id, key = new_job()
    upload(aws, key, clip_path)
    verify(db, job_id)
    if terminal == "failed":
        billing.issue_refund(db, job_id, "cancelled", queued=True)
    else:
        billing.settle_success(db, job_id)
    before = pg.snapshot()

    assert run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small) is worker.Outcome.SKIPPED

    assert pg.snapshot() == before
    assert object_exists(aws, key) == (terminal == "done")


def test_a_busy_job_is_skipped_and_changes_nothing(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path
):
    job_id, key = new_job()
    upload(aws, key, clip_path)
    verify(db, job_id)
    assert billing.claim(db, job_id) == 1
    before = pg.snapshot()

    assert run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small) is worker.Outcome.SKIPPED

    assert pg.snapshot() == before
    assert object_exists(aws, key)
    assert not object_exists(aws, f"results/{job_id}.json")


# ---------------------------------------------------------------- failures


def test_a_failure_refunds_and_deletes_at_once(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    job_id, key = new_job()
    upload(aws, key, clip_path)
    verify(db, job_id)

    def boom(*args, **kwargs):
        raise RuntimeError("GPU exploded")

    patch_everywhere("predict", boom, "neurolens.inference", "neurolens.worker")

    assert run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small) is worker.Outcome.FAILED

    assert_refunded(pg, job_id, "processing_failed")
    for gone in (
        key,
        f"results/{job_id}.json",
        f"results/{job_id}.brain",
        f"results/{job_id}.jpg",
    ):
        assert not object_exists(aws, gone)


def test_an_unreadable_video_is_refunded_and_its_upload_deleted(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, tmp_path
):
    job_id, key = new_job()
    upload(aws, key, clip_path)
    verify(db, job_id)
    garbage = tmp_path / "garbage.mp4"
    garbage.write_bytes(b"not a video at all")

    outcome = worker.handle_job(
        job_id,
        str(garbage),
        s3=aws.s3,
        db=db,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        keep_alive=no_keep_alive,
    )

    assert outcome is worker.Outcome.REJECTED
    assert_refunded(pg, job_id, "unreadable_video")
    assert not object_exists(aws, key)


def test_a_video_longer_than_it_was_checked_is_refunded_video_changed(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path
):
    job_id, key = new_job()
    upload(aws, key, clip_path)
    verify(db, job_id, duration_ms=1000)  # the 3-second clip is now over the checked length + 1 s

    assert run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small) is worker.Outcome.REJECTED

    assert_refunded(pg, job_id, "video_changed")
    assert not object_exists(aws, key)
    for absent in (
        f"results/{job_id}.json",
        f"results/{job_id}.brain",
        f"results/{job_id}.jpg",
    ):
        assert not object_exists(aws, absent)


def test_a_video_within_a_second_of_the_checked_length_runs_normally(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path
):
    job_id, key = new_job()
    upload(aws, key, clip_path)
    verify(db, job_id, duration_ms=2100)  # 900 ms shorter than the clip, within the allowance

    assert run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small) is worker.Outcome.DONE

    job = pg.job(job_id)
    assert job["status"] == "done" and job["captured_cents"] == 90
    assert pg.balance() == (410, 0)
    assert object_exists(aws, key)
    assert object_exists(aws, f"results/{job_id}.json")


def test_a_failed_delete_after_a_refund_never_undoes_the_refund(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    job_id, key = new_job()
    upload(aws, key, clip_path)
    verify(db, job_id)

    def boom(*args, **kwargs):
        raise RuntimeError("GPU exploded")

    patch_everywhere("predict", boom, "neurolens.inference", "neurolens.worker")
    patch_everywhere("delete_keys", lambda *a, **kw: False, "neurolens.storage", "neurolens.worker")

    assert run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small) is worker.Outcome.FAILED

    assert_refunded(pg, job_id, "processing_failed")


def test_a_consented_job_that_lost_its_claim_writes_no_dataset_files(
    aws, db, pg, make_cfg, roi_masks_small, new_key, clip_path, patch_everywhere
):
    job_id, key = consented_job(db, new_key)
    upload(aws, key, clip_path)
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
        run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small) is worker.Outcome.LOST_CLAIM
    )

    job = pg.job(job_id)
    assert job["status"] == "processing" and job["attempt"] == 2
    assert pg.balance() == (410, 90)
    assert object_exists(aws, key)
    for absent in (
        f"dataset/{job_id}/video.mp4",
        f"dataset/{job_id}/result.json",
        f"dataset/{job_id}/brain",
        f"results/{job_id}.json",
    ):
        assert not object_exists(aws, absent)


def test_a_lost_claim_returns_lost_claim_without_refund_or_deletes(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    job_id, key = new_job()
    upload(aws, key, clip_path)
    verify(db, job_id)
    real_build = inference.build_events

    def build_while_another_worker_reclaims(path, *args, **kwargs):
        pg.age(job_id, updated_s=120)
        assert billing.claim(db, job_id) == 2
        return real_build(path, *args, **kwargs)

    patch_everywhere(
        "build_events",
        build_while_another_worker_reclaims,
        "neurolens.inference",
        "neurolens.worker",
    )

    outcome = run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small)

    assert outcome is worker.Outcome.LOST_CLAIM
    job = pg.job(job_id)
    assert job["status"] == "processing" and job["attempt"] == 2
    assert pg.balance() == (410, 90)
    assert object_exists(aws, key)
    assert not object_exists(aws, f"results/{job_id}.json")


def test_a_settlement_that_commits_then_raises_keeps_the_done_result(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    job_id, key = new_job()
    upload(aws, key, clip_path)
    verify(db, job_id)
    real_settle = billing.settle_success

    def settle_then_raise(db, job_id):
        real_settle(db, job_id)
        raise RuntimeError("the settlement response was lost")

    patch_everywhere("settle_success", settle_then_raise, "neurolens.billing", "neurolens.worker")

    assert run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small) is worker.Outcome.FAILED

    job = pg.job(job_id)
    assert job["status"] == "done" and job["captured_cents"] == 90
    assert pg.balance() == (410, 0)
    assert pg.kinds().count("capture") == 1 and "refund" not in pg.kinds()
    for kept in (
        key,
        f"results/{job_id}.json",
        f"results/{job_id}.brain",
        f"results/{job_id}.jpg",
    ):
        assert object_exists(aws, kept)


def test_a_job_that_was_never_dispatched_is_refunded_not_verified(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path
):
    job_id, key = new_job()
    upload(aws, key, clip_path)  # no verify_queued call: verified_duration_ms stays NULL

    assert run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small) is worker.Outcome.REJECTED

    assert_refunded(pg, job_id, "not_verified")
    assert not object_exists(aws, f"results/{job_id}.json")


# ---------------------------------------------------------------- keep-alive and timeout


def test_claim_keep_alive_stops_at_max_seconds():
    beats = []
    started = time.monotonic()

    with worker.ClaimKeepAlive(lambda: beats.append(time.monotonic()), 0.02, 0.1):
        time.sleep(0.25)

    count = len(beats)
    time.sleep(0.15)
    assert count >= 1
    assert len(beats) == count
    assert max(beats) - started < 0.5


def test_claim_keep_alive_calls_on_overrun_once_at_max_seconds():
    beats = []
    overruns = []
    started = time.monotonic()

    with worker.ClaimKeepAlive(
        lambda: beats.append(time.monotonic()),
        0.02,
        0.1,
        lambda: overruns.append(time.monotonic()),
    ):
        time.sleep(0.25)

    count = len(beats)
    time.sleep(0.15)
    assert len(overruns) == 1
    assert count >= 1
    assert len(beats) == count
    assert max(beats) - started < 0.5


def test_a_job_over_the_limit_ends_the_process(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, monkeypatch
):
    job_id, key = new_job()
    upload(aws, key, clip_path)
    verify(db, job_id)

    exits = []
    monkeypatch.setattr(os, "_exit", lambda code: exits.append(code))
    captured = {}

    class CapturingKeepAlive:
        def __init__(self, on_beat, interval_seconds, max_seconds, on_overrun=None):
            captured["on_beat"] = on_beat
            captured["on_overrun"] = on_overrun

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(worker, "ClaimKeepAlive", CapturingKeepAlive)

    assert (
        worker.handle_job(
            job_id,
            str(clip_path),
            s3=aws.s3,
            db=db,
            cfg=make_cfg(),
            roi_masks=roi_masks_small,
            keep_alive=None,
        )
        is worker.Outcome.DONE
    )

    assert captured["on_overrun"] is not None
    captured["on_overrun"]()
    assert exits == [1]


def test_max_job_minutes_finishes_before_the_invocation_timeout():
    cfg = settings.load_config()
    assert cfg["worker"]["max_job_minutes"] * 60 < 3600


# ---------------------------------------------------------------- the SageMaker server


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


def raw_invocation(server, request, *, timeout=5):
    host, port = server.server_address[:2]
    with socket.create_connection((host, port), timeout=timeout) as connection:
        connection.sendall(request)
        connection.shutdown(socket.SHUT_WR)
        chunks = []
        while chunk := connection.recv(64 * 1024):
            chunks.append(chunk)
    response = b"".join(chunks)
    return int(response.split(b" ", 2)[1]), response


@pytest.fixture
def server(aws, db, make_cfg, roi_masks_small):
    instance = worker.make_server(
        "127.0.0.1",
        0,
        s3=aws.s3,
        db=db,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
    )
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


@pytest.fixture
def fake_handle(monkeypatch):
    state = SimpleNamespace(
        calls=[],
        started=threading.Event(),
        release=threading.Event(),
        block=False,
        outcome=worker.Outcome.DONE,
        body=None,
        video_path=None,
        existed=False,
    )

    def fake(job_id, video_path, *, s3, db, cfg, roi_masks, keep_alive=None):
        state.calls.append(job_id)
        state.video_path = video_path
        state.existed = Path(video_path).exists()
        state.body = Path(video_path).read_bytes()
        if state.block:
            state.started.set()
            state.release.wait(10)
        return state.outcome

    monkeypatch.setattr(worker, "handle_job", fake)
    return state


def test_ping_answers_200(server):
    assert http(server, "GET", "/ping")[0] == 200


def test_the_body_is_streamed_to_a_file_and_answered_with_json(server, fake_handle):
    job_id = str(uuid.uuid4())
    status, body = http(
        server,
        "POST",
        "/invocations",
        body=b"the video bytes",
        headers={JOB_HEADER: job_id},
    )
    assert status == 200
    assert json.loads(body) == {"job_id": job_id, "outcome": "done"}
    assert fake_handle.calls == [job_id]
    assert fake_handle.existed and fake_handle.body == b"the video bytes"


def test_a_request_without_a_content_length_is_a_500_and_never_claims(server, fake_handle):
    job_id = str(uuid.uuid4())
    request = (
        b"POST /invocations HTTP/1.0\r\n"
        b"Host: localhost\r\n"
        + f"{JOB_HEADER}: {job_id}\r\n".encode()
        + b"\r\n"
        + b"the video bytes"
    )

    status, _ = raw_invocation(server, request)

    assert status == 500
    assert fake_handle.calls == []


def test_a_body_shorter_than_its_content_length_is_a_500_and_never_claims(server, fake_handle):
    job_id = str(uuid.uuid4())
    body = b"short"
    request = (
        b"POST /invocations HTTP/1.0\r\n"
        b"Host: localhost\r\n"
        + f"{JOB_HEADER}: {job_id}\r\n".encode()
        + f"Content-Length: {len(body) + 100}\r\n".encode()
        + b"\r\n"
        + body
    )

    status, _ = raw_invocation(server, request)

    assert status == 500
    assert fake_handle.calls == []


def test_a_body_that_stalls_is_a_500_and_frees_the_busy_lock(server, fake_handle, monkeypatch):
    monkeypatch.setattr(worker, "BODY_READ_TIMEOUT_S", 0.3)
    job_id = str(uuid.uuid4())
    host, port = server.server_address[:2]
    with socket.create_connection((host, port), timeout=5) as connection:
        connection.sendall(
            b"POST /invocations HTTP/1.0\r\n"
            b"Host: localhost\r\n"
            + f"{JOB_HEADER}: {job_id}\r\n".encode()
            + b"Content-Length: 100\r\n"
            + b"\r\n"
            + b"0123456789"
        )
        response = connection.recv(64 * 1024)

    assert int(response.split(b" ", 2)[1]) == 500
    assert fake_handle.calls == []

    next_job = str(uuid.uuid4())
    status = None
    for _ in range(20):
        headers = {JOB_HEADER: next_job}
        status, _ = http(server, "POST", "/invocations", body=b"video", headers=headers)
        if status != 503:
            break
        time.sleep(0.05)
    assert status == 200
    assert fake_handle.calls == [next_job]


@pytest.mark.parametrize("bad_id", [None, "", "not-a-uuid"])
def test_a_missing_or_malformed_job_id_is_a_400(server, fake_handle, bad_id):
    headers = {} if bad_id is None else {JOB_HEADER: bad_id}
    status, _ = http(server, "POST", "/invocations", body=b"video", headers=headers)
    assert status == 400
    assert fake_handle.calls == []


@pytest.mark.parametrize(
    "outcome_name,status",
    [
        ("done", 200),
        ("duplicate", 200),
        ("rejected", 200),
        ("skipped", 200),
        ("failed", 500),
        ("lost_claim", 500),
    ],
)
def test_each_outcome_gets_its_http_status(server, fake_handle, outcome_name, status):
    fake_handle.outcome = getattr(worker.Outcome, outcome_name.upper())
    job_id = str(uuid.uuid4())

    code, body = http(server, "POST", "/invocations", body=b"video", headers={JOB_HEADER: job_id})

    assert code == status
    assert json.loads(body) == {"job_id": job_id, "outcome": outcome_name}


def test_a_busy_container_refuses_a_second_job_but_still_answers_ping(server, fake_handle):
    fake_handle.block = True
    first = []
    first_job = str(uuid.uuid4())

    def send_first():
        first.append(
            http(server, "POST", "/invocations", body=b"first", headers={JOB_HEADER: first_job})
        )

    thread = threading.Thread(target=send_first)
    thread.start()
    try:
        assert fake_handle.started.wait(5)
        assert http(server, "GET", "/ping")[0] == 200
        status, _ = http(
            server,
            "POST",
            "/invocations",
            body=b"second",
            headers={JOB_HEADER: str(uuid.uuid4())},
        )
        assert status == 503
    finally:
        fake_handle.release.set()
        thread.join(timeout=10)

    assert first and first[0][0] == 200
    assert fake_handle.calls == [first_job]


def test_a_second_invocation_on_the_same_server_still_works(
    aws, db, make_cfg, roi_masks_small, monkeypatch, tmp_path
):
    cache = tmp_path / "features"
    cache.mkdir()
    instance = worker.make_server(
        "127.0.0.1",
        0,
        s3=aws.s3,
        db=db,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
        feature_cache=str(cache),
    )
    created = []

    def fake(job_id, video_path, *, s3, db, cfg, roi_masks, keep_alive=None):
        if not created:
            (cache / "extractor").mkdir()
            created.append(True)
        (cache / "extractor" / f"{job_id}.npy").write_bytes(b"features")
        return worker.Outcome.DONE

    monkeypatch.setattr(worker, "handle_job", fake)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        for job_id in (first, second):
            code, _ = http(
                instance,
                "POST",
                "/invocations",
                body=b"video",
                headers={JOB_HEADER: job_id},
            )
            assert code == 200

        assert (cache / "extractor" / f"{first}.npy").exists()
        assert (cache / "extractor" / f"{second}.npy").exists()
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)
