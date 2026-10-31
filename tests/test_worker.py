"""The GPU container's job logic (`worker.handle_job`), from the merged M1, M2b, M3a and M3d
worker tests (docs/M3e_spec.md §4b, §4c, §11 "Container").

SageMaker has already downloaded the upload, so handle_job gets a local path and takes the object
key and verified duration from the job row. A test that runs the pipeline therefore verifies the
job first, exactly as the dispatcher does. Fake inference, moto S3 and PostgreSQL; money and job
state are read directly through `pg`.

Checks that moved to the dispatcher (size, duration, credit) or that no longer exist (SQS polling,
release for retry, a missing upload) are gone; the SageMaker server's HTTP contract lives in
tests/test_m3e_container.py.
"""

import contextlib
import inspect
import json
import math
import subprocess
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from conftest import STARTER_CENTS
from neurolens import billing, inference, pricing, settings, storage, worker
from neurolens.media import probe_duration as _real_probe_duration  # before tests patch it
from neurolens.worker import Outcome

SERIES = [
    "faces",
    "bodies",
    "scenes",
    "visual",
    "sound",
    "speech",
    "social",
    "see",
    "hear",
    "understand",
    "overall",
]
SECTION_1_KEYS = {
    "format",
    "duration_seconds",
    "signals",
    "muted",
    "job_id",
    "processing_time_seconds",
    "fake_inference",
}
STAGES = [
    "transcribing",
    "inference_full",
    "inference_noaudio",
    "extracting_roi",
    "saving_result",
]
MODULES = ("neurolens.inference", "neurolens.worker")
PROBE_MODULES = ("neurolens.inference", "neurolens.media", "neurolens.worker")


@pytest.fixture(autouse=True)
def fake_on(monkeypatch):
    monkeypatch.setenv("FAKE_INFERENCE", "1")


@pytest.fixture(autouse=True)
def no_gpu(patch_everywhere):
    """gpu_info() needs torch, which the test venv does not have: report 'no GPU'."""
    patch_everywhere("gpu_info", lambda: None, *MODULES)


def probe_duration(path, *args, **kwargs):
    return _real_probe_duration(path, *args, **kwargs)


def no_keep_alive(on_beat):
    return contextlib.nullcontext()


def verify(db, job_id, duration_ms=3000):
    assert billing.verify_queued(db, job_id, duration_ms, settings.max_duration(None)) == "ok"


def run_job(aws, db, job_id, clip_path, make_cfg, roi_masks, keep_alive=no_keep_alive):
    return worker.handle_job(
        job_id,
        str(clip_path),
        s3=aws.s3,
        db=db,
        cfg=make_cfg(),
        roi_masks=roi_masks,
        keep_alive=keep_alive,
    )


def has_audio(path):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "a", "-show_entries", "stream=codec_type"]
        + ["-of", "json", str(path)],
        capture_output=True,
        text=True,
    )
    return bool(json.loads(out.stdout).get("streams"))


@pytest.fixture
def calls(patch_everywhere):
    """Spies on the inference-side functions. Each wraps the real (fake-mode) function.

    `order` records every call by name, in the order the worker made them.
    """
    log = {
        "order": [],
        "build_events": [],
        "without_audio": [],
        "predict": [],
        "probe_duration": [],
        "extract": [],
        "extract_args": [],
        "files_at_extract": [],  # the job's working folder, as it is when extraction starts
    }
    real_build = inference.build_events
    real_without = inference.without_audio
    real_predict = inference.predict
    real_probe = probe_duration
    from neurolens import response

    real_extract = response.extract_response

    def build(path, *a, **kw):
        log["order"].append("build_events")
        events = real_build(path, *a, **kw)
        log["build_events"].append((str(path), has_audio(path), Path(path).read_bytes(), events))
        return events

    def without(events, *a, **kw):
        log["order"].append("without_audio")
        out = real_without(events, *a, **kw)
        log["without_audio"].append((events, out))
        return out

    def predict(events, duration, *a, **kw):
        log["order"].append("predict")
        preds = real_predict(events, duration, *a, **kw)
        log["predict"].append((events, duration, preds))
        return preds

    def probe(path, *a, **kw):
        log["probe_duration"].append(str(path))
        return real_probe(path, *a, **kw)

    def extract(*a, **kw):
        log["order"].append("extract_response")
        bound = inspect.signature(real_extract).bind(*a, **kw)
        log["extract_args"].append(list(bound.arguments.values()))
        if log["build_events"]:
            folder = Path(log["build_events"][0][0]).parent
            log["files_at_extract"].extend(p.name for p in folder.iterdir())
        result = real_extract(*a, **kw)
        log["extract"].append(result)
        return result

    patch_everywhere("build_events", build, *MODULES)
    patch_everywhere("without_audio", without, *MODULES)
    patch_everywhere("predict", predict, *MODULES)
    patch_everywhere("probe_duration", probe, *PROBE_MODULES)
    patch_everywhere("extract_response", extract, "neurolens.response", "neurolens.worker")
    return log


@pytest.fixture
def ffmpeg_runs(monkeypatch):
    """Records every ffmpeg (not ffprobe) command run through subprocess, then runs it."""
    seen = []
    real = subprocess.run

    def spy(cmd, *a, **kw):
        if isinstance(cmd, list | tuple) and cmd and Path(str(cmd[0])).name == "ffmpeg":
            seen.append(list(cmd))
        return real(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    return seen


def upload(aws, key, data):
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=data)


def upload_clip(aws, key, clip_path):
    aws.s3.upload_file(str(clip_path), aws.bucket, key)


def object_exists(aws, key):
    try:
        aws.s3.head_object(Bucket=aws.bucket, Key=key)
    except ClientError as err:
        assert err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound")
        return False
    return True


def read_result(aws, job_id):
    """The result as published to S3 (results/{job_id}.json, not the local folder)."""
    try:
        body = aws.s3.get_object(Bucket=aws.bucket, Key=f"results/{job_id}.json")["Body"]
    except ClientError as err:
        raise AssertionError(f"no result object results/{job_id}.json in S3") from err
    return json.loads(body.read())


def never_inference(patch_everywhere):
    def boom(*a, **kw):
        raise AssertionError("inference must not run")

    patch_everywhere("build_events", boom, *MODULES)
    patch_everywhere("without_audio", boom, *MODULES)
    patch_everywhere("predict", boom, *MODULES)


def source_arc(db, job_id):
    """(status, stage) as the row reads while a pipeline step runs."""
    with db.transaction() as tx:
        [job] = tx.execute(
            "SELECT status, stage FROM jobs WHERE job_id = CAST(:job_id AS uuid)",
            {"job_id": job_id},
        )
    return job["status"], job["stage"]


# ---------------------------------------------------------------- DONE


def test_valid_video_is_done_and_writes_result_json(
    aws, make_cfg, roi_masks_small, db, new_job, clip_path, calls
):
    job_id, key = new_job()
    upload_clip(aws, key, clip_path)
    verify(db, job_id)

    outcome = run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small)

    assert outcome is Outcome.DONE
    result = read_result(aws, job_id)
    assert set(result) == SECTION_1_KEYS | {"frames"}  # exactly these: no filename, no gpu
    assert result["job_id"] == job_id
    assert result["fake_inference"] is True
    assert isinstance(result["processing_time_seconds"], int | float)
    assert result["processing_time_seconds"] >= 0
    expected_seconds = math.ceil(probe_duration(clip_path))
    assert result["format"] == 2
    assert result["duration_seconds"] == expected_seconds
    assert list(result["signals"]) == SERIES
    assert list(result["muted"]) == SERIES
    assert len(result["signals"]["overall"]) == expected_seconds


def test_duration_and_signals_are_exactly_what_extract_response_returned(
    aws, make_cfg, roi_masks_small, db, new_job, clip_path, calls
):
    job_id, key = new_job()
    upload_clip(aws, key, clip_path)
    verify(db, job_id)
    run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small)

    result = read_result(aws, job_id)
    assert len(calls["extract"]) == 1
    expected = json.loads(json.dumps(calls["extract"][0]))
    assert result["duration_seconds"] == expected["duration_seconds"]
    assert result["signals"] == expected["signals"]
    assert result["muted"] == expected["muted"]


def test_result_goes_to_s3_as_results_job_id_json(
    aws, make_cfg, roi_masks_small, db, new_job, clip_path
):
    job_id, key = new_job(".mov")
    upload_clip(aws, key, clip_path)
    verify(db, job_id)

    run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small)

    assert object_exists(aws, f"results/{job_id}.json")


def test_pipeline_runs_build_events_predict_without_audio_predict_extract_in_order(
    aws, make_cfg, roi_masks_small, db, new_job, clip_path, calls
):
    job_id, key = new_job()
    upload_clip(aws, key, clip_path)
    verify(db, job_id)
    run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small)

    assert calls["order"] == [
        "build_events",
        "predict",
        "without_audio",
        "predict",
        "extract_response",
    ]


def test_pipeline_passes_events_and_predictions_along(
    aws, make_cfg, roi_masks_small, db, new_job, clip_path, calls
):
    """build_events gets the local video (with its audio); the first predict gets those events,
    without_audio gets them too, the second predict gets what without_audio returned, both get
    the measured duration, and extract_response gets (with-audio, no-audio, masks)."""
    job_id, key = new_job()
    upload_clip(aws, key, clip_path)
    verify(db, job_id)
    run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small)

    [(_, audio, content, events)] = calls["build_events"]
    assert audio is True  # the video itself, with its soundtrack
    assert content == clip_path.read_bytes()
    [(given, stripped)] = calls["without_audio"]
    assert given is events
    (first_events, first_duration, full), (second_events, second_duration, noaudio) = calls[
        "predict"
    ]
    assert first_events is events
    assert second_events is stripped
    expected_duration = probe_duration(clip_path)
    assert first_duration == pytest.approx(expected_duration)
    assert second_duration == pytest.approx(expected_duration)
    [args] = calls["extract_args"]
    assert args[0] is full
    assert args[1] is noaudio
    assert args[2] is roi_masks_small


def test_pipeline_writes_no_noaudio_file_and_runs_no_ffmpeg(
    aws, make_cfg, roi_masks_small, db, new_job, clip_path, calls, ffmpeg_runs
):
    """The no-audio pass reuses the same video file: no audio-free copy is made. The one ffmpeg
    run, the thumbnail, writes to a pipe, never to a file."""
    job_id, key = new_job()
    upload_clip(aws, key, clip_path)
    verify(db, job_id)
    outcome = run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small)

    assert outcome is Outcome.DONE
    seen_files = calls["files_at_extract"]
    assert seen_files, "extract_response was not reached"
    assert not [name for name in seen_files if "noaudio" in name]
    assert all(cmd[-1] == "-" for cmd in ffmpeg_runs)


def test_result_has_gpu_only_when_gpu_info_returns_a_value(
    aws, make_cfg, roi_masks_small, db, new_job, clip_path, patch_everywhere
):
    gpu = {"device": "Fake GPU 48GB", "peak_vram_gb": 12.34}
    patch_everywhere("gpu_info", lambda: gpu, *MODULES)
    job_id, key = new_job()
    upload_clip(aws, key, clip_path)
    verify(db, job_id)
    run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small)

    result = read_result(aws, job_id)
    assert set(result) == SECTION_1_KEYS | {"frames", "gpu"}
    assert result["gpu"] == gpu


def test_fake_inference_key_is_absent_when_fake_mode_is_off(
    aws, make_cfg, roi_masks_small, db, new_job, clip_path, patch_everywhere, monkeypatch
):
    """fake_inference is present (and true) only in fake mode. The real model is replaced by
    stand-ins (events record the path; predict returns random numbers), so this runs with no
    torch or tribev2."""
    import numpy as np

    def build_stand_in(path):
        return {"path": str(path), "audio": True}

    def without_stand_in(events):
        return {**events, "audio": False}

    def predict_stand_in(events, duration):
        return np.random.default_rng(0).random((math.ceil(duration), 20484))

    patch_everywhere("build_events", build_stand_in, *MODULES)
    patch_everywhere("without_audio", without_stand_in, *MODULES)
    patch_everywhere("predict", predict_stand_in, *MODULES)
    monkeypatch.delenv("FAKE_INFERENCE")
    job_id, key = new_job()
    upload_clip(aws, key, clip_path)
    verify(db, job_id)
    outcome = run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small)

    assert outcome is Outcome.DONE
    result = read_result(aws, job_id)
    assert "fake_inference" not in result
    assert set(result) == SECTION_1_KEYS - {"fake_inference"}


def test_result_is_written_before_the_job_is_settled(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, monkeypatch
):
    """put_result first, then settle_success (status done)."""
    job_id, key = new_job()
    upload_clip(aws, key, clip_path)
    verify(db, job_id)
    at_put_result = []
    real = storage.put_result

    def spy(s3, bucket, jid, result):
        at_put_result.append(pg.job(jid)["status"])
        return real(s3, bucket, jid, result)

    monkeypatch.setattr(storage, "put_result", spy)
    if hasattr(worker, "put_result"):
        monkeypatch.setattr(worker, "put_result", spy)
    assert run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small) is Outcome.DONE
    assert len(at_put_result) == 1
    assert at_put_result[0] != "done"
    assert pg.job(job_id)["status"] == "done"


class TrackingKeepAlive:
    """Stands in for the keep-alive factory handle_job gets: records whether a block is active
    and the on_beat it was given."""

    def __init__(self):
        self.active = False
        self.entered = 0
        self.on_beats = []

    def __call__(self, on_beat=None):
        self.on_beats.append(on_beat)
        return self

    def __enter__(self):
        self.active = True
        self.entered += 1
        return self

    def __exit__(self, *exc):
        self.active = False
        return False


def test_record_work_runs_inside_the_keep_alive(
    aws, db, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    job_id, key = new_job()
    upload_clip(aws, key, clip_path)
    verify(db, job_id)
    keep_alive = TrackingKeepAlive()
    inside = []
    real_build, real_predict = inference.build_events, inference.predict

    def build(path, *a, **kw):
        inside.append(keep_alive.active)
        return real_build(path, *a, **kw)

    def predict(events, duration, *a, **kw):
        inside.append(keep_alive.active)
        return real_predict(events, duration, *a, **kw)

    patch_everywhere("build_events", build, *MODULES)
    patch_everywhere("predict", predict, *MODULES)
    assert (
        run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small, keep_alive) is Outcome.DONE
    )
    assert inside == [True, True, True]
    assert keep_alive.active is False  # left before returning
    assert len(keep_alive.on_beats) == 1 and callable(keep_alive.on_beats[0])


def test_the_keep_alive_touches_the_claimed_job(
    aws, db, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    job_id, key = new_job()
    upload_clip(aws, key, clip_path)
    verify(db, job_id)
    touched = []
    real_touch = billing.touch

    def spy(db, job_id, attempt):
        touched.append((job_id, attempt))
        return real_touch(db, job_id, attempt)

    patch_everywhere("touch", spy, "neurolens.billing", "neurolens.worker")
    real_predict = inference.predict

    def slow_predict(*a, **kw):
        import time

        time.sleep(0.3)
        return real_predict(*a, **kw)

    patch_everywhere("predict", slow_predict, *MODULES)

    def keep_alive(on_beat):
        return worker.ClaimKeepAlive(on_beat, 0.05, 60)

    assert (
        run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small, keep_alive) is Outcome.DONE
    )
    assert touched, "no beat touched the job"
    assert set(touched) == {(job_id, 1)}


# ---------------------------------------------------------------- stages


def test_stages_are_recorded_in_order(aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path):
    job_id, key = new_job()
    upload_clip(aws, key, clip_path)
    verify(db, job_id)
    assert run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small) is Outcome.DONE

    job = pg.job(job_id)
    assert [stage["stage"] for stage in job["stages"]] == STAGES
    times = [stage["at"] for stage in job["stages"]]
    assert times == sorted(times)


def test_each_pipeline_step_runs_under_its_own_stage(
    aws, db, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    job_id, key = new_job()
    upload_clip(aws, key, clip_path)
    verify(db, job_id)
    seen = {}

    real_build, real_predict = inference.build_events, inference.predict
    from neurolens import response

    real_extract = response.extract_response

    def build(path, *a, **kw):
        seen["build_events"] = source_arc(db, job_id)
        return real_build(path, *a, **kw)

    def predict(events, duration, *a, **kw):
        seen.setdefault("predict", []).append(source_arc(db, job_id))
        return real_predict(events, duration, *a, **kw)

    def extract(*a, **kw):
        seen["extract"] = source_arc(db, job_id)
        return real_extract(*a, **kw)

    patch_everywhere("build_events", build, *MODULES)
    patch_everywhere("predict", predict, *MODULES)
    patch_everywhere("extract_response", extract, "neurolens.response", "neurolens.worker")

    outcome = run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small)
    assert outcome is Outcome.DONE
    assert seen["build_events"] == ("processing", "transcribing")
    assert seen["predict"] == [
        ("processing", "inference_full"),
        ("processing", "inference_noaudio"),
    ]
    assert seen["extract"] == ("processing", "extracting_roi")


def test_duplicate_is_charged_once_and_leaves_the_other_result(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    """Another worker publishes first (its settlement never ran): this worker's put_result loses
    the conditional write, and its settle_success makes sure the finished job is charged once."""
    job_id, key = new_job()
    upload_clip(aws, key, clip_path)
    verify(db, job_id)
    theirs = {"job_id": job_id, "marker": "the other worker"}
    real_predict = inference.predict

    def predict(events, duration, *a, **kw):
        if not storage.result_exists(aws.s3, aws.bucket, job_id):
            assert storage.put_result(aws.s3, aws.bucket, job_id, theirs) is True
        return real_predict(events, duration, *a, **kw)

    patch_everywhere("predict", predict, *MODULES)
    assert run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small) is Outcome.DUPLICATE
    body = aws.s3.get_object(Bucket=aws.bucket, Key=f"results/{job_id}.json")["Body"]
    assert json.loads(body.read()) == theirs
    job = pg.job(job_id)
    price = pricing.estimate_cost_cents(3)
    assert job["status"] == "done" and job["captured_cents"] == price
    assert pg.kinds().count("capture") == 1
    assert pg.balance() == (STARTER_CENTS - price, 0)


# ---------------------------------------------------------------- failures and lost claims


def test_a_pipeline_failure_returns_failed_with_a_short_error(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    """The container refunds at once, deletes the files and answers FAILED (spec §4c). The
    user-facing error is the first line, never a traceback and never over 200 characters."""
    job_id, key = new_job()
    upload_clip(aws, key, clip_path)
    verify(db, job_id)
    huge = "line one of a long error\n" + "x" * 5000

    def boom(*a, **kw):
        raise RuntimeError(huge)

    patch_everywhere("predict", boom, *MODULES)
    assert run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small) is Outcome.FAILED

    job = pg.job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "processing_failed"
    message = job["error_message"]
    assert isinstance(message, str) and message
    assert len(message) <= 200
    assert "Traceback" not in message
    assert pg.balance() == (STARTER_CENTS, 0)
    assert not object_exists(aws, key)
    assert not object_exists(aws, f"results/{job_id}.json")


def test_a_claim_lost_mid_pipeline_stops_with_lost_claim_and_moves_no_money(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    job_id, key = new_job()
    upload_clip(aws, key, clip_path)
    verify(db, job_id)
    from neurolens import response

    real_extract = response.extract_response

    def extract_then_lose(*args, **kwargs):
        result = real_extract(*args, **kwargs)
        pg.age(job_id, updated_s=120)  # this worker looked dead ...
        assert billing.claim(db, job_id) == 2  # ... and another took the job over
        return result

    patch_everywhere(
        "extract_response", extract_then_lose, "neurolens.response", "neurolens.worker"
    )
    assert run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small) is Outcome.LOST_CLAIM
    job = pg.job(job_id)
    assert job["status"] == "processing" and job["attempt"] == 2
    price = pricing.estimate_cost_cents(3)
    assert pg.balance() == (STARTER_CENTS - price, price)
    assert not object_exists(aws, f"results/{job_id}.json")


def test_a_job_refunded_mid_pipeline_is_a_lost_claim(
    aws, db, pg, make_cfg, roi_masks_small, new_job, clip_path, patch_everywhere
):
    job_id, key = new_job()
    upload_clip(aws, key, clip_path)
    verify(db, job_id)
    real_probe = probe_duration

    def probe_while_the_reaper_refunds(path, *a, **kw):
        pg.age(job_id, updated_s=700)
        assert billing.issue_refund(db, job_id, "stalled", processing_stale_s=600) is True
        return real_probe(path, *a, **kw)

    patch_everywhere("probe_duration", probe_while_the_reaper_refunds, *PROBE_MODULES)
    never_inference(patch_everywhere)  # but probe already ran: keep its real call above
    assert run_job(aws, db, job_id, clip_path, make_cfg, roi_masks_small) is Outcome.LOST_CLAIM
    job = pg.job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "stalled"
    assert pg.refund_reason(job_id) == "stalled"  # refunded once, by the reaper alone


# ---------------------------------------------------------------- config


MISSING_KEYS = [
    ("aws", "region"),
    ("aws", "s3_bucket"),
    ("worker", "heartbeat_seconds"),
    ("worker", "max_job_minutes"),
]


@pytest.mark.parametrize("section, leaf", MISSING_KEYS, ids=[k[1] for k in MISSING_KEYS])
def test_validate_config_names_each_missing_key(make_cfg, section, leaf):
    cfg = make_cfg()
    if section is None:
        del cfg[leaf]
    else:
        del cfg[section][leaf]
    with pytest.raises(ValueError, match=leaf):
        worker.validate_config(cfg)


def test_validate_config_accepts_a_complete_config(make_cfg):
    worker.validate_config(make_cfg())


def test_validate_config_refuses_a_heartbeat_over_50_seconds(make_cfg):
    cfg = make_cfg(worker={"heartbeat_seconds": 75, "max_job_minutes": 55})
    with pytest.raises(ValueError, match="heartbeat_seconds"):
        worker.validate_config(cfg)
