"""Tests for neurolens.worker (moto, fake inference). Written from docs/M1_spec.md 1, 4a, 5, 6a."""

import json
import math
import subprocess
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from neurolens import inference, worker
from neurolens.worker import Outcome, handle_record, process_message

SECTION_1_KEYS = {
    "duration_seconds",
    "timesteps",
    "job_id",
    "processing_time_seconds",
    "fake_inference",
}
MODULES = ("neurolens.inference", "neurolens.worker")


@pytest.fixture(autouse=True)
def fake_on(monkeypatch):
    monkeypatch.setenv("FAKE_INFERENCE", "1")


@pytest.fixture(autouse=True)
def no_gpu(patch_everywhere):
    """gpu_info() needs torch, which the test venv does not have: report 'no GPU'."""
    patch_everywhere("gpu_info", lambda: None, *MODULES)


def s3_event(*pairs):
    return json.dumps(
        {
            "Records": [
                {"s3": {"bucket": {"name": b}, "object": {"key": k, "size": 1}}} for b, k in pairs
            ]
        }
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
    """Spies on the inference-side functions. Each wraps the real (fake-mode) function."""
    log = {"run_inference": [], "strip_audio": [], "probe_duration": [], "extract": []}
    real_run = inference.run_inference
    real_strip = inference.strip_audio
    real_probe = inference.probe_duration
    from neurolens import engagement

    real_extract = engagement.extract_engagement

    def run(path, *a, **kw):
        log["run_inference"].append((str(path), has_audio(path)))
        return real_run(path, *a, **kw)

    def strip(src, dst, *a, **kw):
        log["strip_audio"].append((str(src), str(dst)))
        return real_strip(src, dst, *a, **kw)

    def probe(path, *a, **kw):
        log["probe_duration"].append(str(path))
        return real_probe(path, *a, **kw)

    def extract(*a, **kw):
        result = real_extract(*a, **kw)
        log["extract"].append(result)
        return result

    patch_everywhere("run_inference", run, *MODULES)
    patch_everywhere("strip_audio", strip, *MODULES)
    patch_everywhere("probe_duration", probe, *MODULES)
    patch_everywhere("extract_engagement", extract, "neurolens.engagement", "neurolens.worker")
    return log


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


def read_result(cfg, job_id):
    path = Path(cfg["paths"]["output"]) / f"{job_id}.json"
    assert path.exists(), f"no result file at {path}"
    return json.loads(path.read_text())


def never_inference(patch_everywhere):
    def boom(*a, **kw):
        raise AssertionError("inference must not run")

    patch_everywhere("run_inference", boom, *MODULES)
    patch_everywhere("strip_audio", boom, *MODULES)


# ---------------------------------------------------------------- Outcome


def test_outcome_has_done_and_rejected():
    assert Outcome.DONE is not Outcome.REJECTED
    assert {"DONE", "REJECTED"} <= set(Outcome.__members__)


# ---------------------------------------------------------------- handle_record: DONE


def test_valid_video_is_done_and_writes_result_json(
    aws, make_cfg, roi_masks_small, new_key, clip_path, calls
):
    cfg = make_cfg()
    job_id, key = new_key()
    upload_clip(aws, key, clip_path)

    outcome = handle_record(aws.bucket, key, s3=aws.s3, cfg=cfg, roi_masks=roi_masks_small)

    assert outcome is Outcome.DONE
    result = read_result(cfg, job_id)
    assert set(result) == SECTION_1_KEYS  # exactly these: no filename, no gpu
    assert result["job_id"] == job_id
    assert result["fake_inference"] is True
    assert isinstance(result["processing_time_seconds"], int | float)
    assert result["processing_time_seconds"] >= 0
    expected_seconds = math.ceil(inference.probe_duration(clip_path))
    assert result["duration_seconds"] == expected_seconds
    assert len(result["timesteps"]) == expected_seconds
    step = result["timesteps"][0]
    assert set(step) == {
        "t",
        "engagement_overall",
        "regions",
        "auditory_with_audio",
        "auditory_without_audio",
    }
    assert set(step["regions"]) == {
        "ffa_faces",
        "eba_bodies",
        "ppa_scenes",
        "sts_social",
        "auditory",
    }


def test_duration_and_timesteps_are_exactly_what_extract_engagement_returned(
    aws, make_cfg, roi_masks_small, new_key, clip_path, calls
):
    cfg = make_cfg()
    job_id, key = new_key()
    upload_clip(aws, key, clip_path)
    handle_record(aws.bucket, key, s3=aws.s3, cfg=cfg, roi_masks=roi_masks_small)
    result = read_result(cfg, job_id)
    assert len(calls["extract"]) == 1
    expected = json.loads(json.dumps(calls["extract"][0]))
    assert result["duration_seconds"] == expected["duration_seconds"]
    assert result["timesteps"] == expected["timesteps"]


def test_result_is_written_under_paths_output_as_job_id_json(
    aws, make_cfg, roi_masks_small, new_key, clip_path, tmp_path
):
    out_dir = tmp_path / "elsewhere" / "results_here"
    out_dir.mkdir(parents=True)
    cfg = make_cfg(paths={**make_cfg()["paths"], "output": str(out_dir)})
    job_id, key = new_key(".mov")
    upload_clip(aws, key, clip_path)
    handle_record(aws.bucket, key, s3=aws.s3, cfg=cfg, roi_masks=roi_masks_small)
    assert [p.name for p in out_dir.iterdir()] == [f"{job_id}.json"]


def test_pipeline_runs_inference_then_strip_audio_then_inference_without_audio(
    aws, make_cfg, roi_masks_small, new_key, clip_path, calls
):
    cfg = make_cfg()
    _, key = new_key()
    upload_clip(aws, key, clip_path)
    handle_record(aws.bucket, key, s3=aws.s3, cfg=cfg, roi_masks=roi_masks_small)
    assert len(calls["strip_audio"]) == 1
    assert len(calls["run_inference"]) == 2
    (first_path, first_audio), (second_path, second_audio) = calls["run_inference"]
    assert first_audio is True  # the full video, with its audio
    assert second_audio is False  # the stripped copy
    assert second_path == calls["strip_audio"][0][1]
    assert first_path == calls["strip_audio"][0][0]


def test_size_check_uses_head_object_before_download_file(
    aws, make_cfg, roi_masks_small, new_key, clip_path, spy_s3
):
    cfg = make_cfg()
    _, key = new_key()
    upload_clip(aws, key, clip_path)
    s3 = spy_s3()
    handle_record(aws.bucket, key, s3=s3, cfg=cfg, roi_masks=roi_masks_small)
    names = s3.names()
    assert "head_object" in names and "download_file" in names
    assert names.index("head_object") < names.index("download_file")
    head = next(c for c in s3.calls if c[0] == "head_object")
    assert head[2] == {"Bucket": aws.bucket, "Key": key}
    download = next(c for c in s3.calls if c[0] == "download_file")
    assert download[1][:2] == (aws.bucket, key)


def test_object_exactly_at_the_cap_is_accepted(aws, make_cfg, roi_masks_small, new_key, clip_path):
    cfg = make_cfg(max_upload_bytes=clip_path.stat().st_size)
    _, key = new_key()
    upload_clip(aws, key, clip_path)
    outcome = handle_record(aws.bucket, key, s3=aws.s3, cfg=cfg, roi_masks=roi_masks_small)
    assert outcome is Outcome.DONE


def test_result_has_gpu_only_when_gpu_info_returns_a_value(
    aws, make_cfg, roi_masks_small, new_key, clip_path, patch_everywhere
):
    gpu = {"device": "Fake GPU 48GB", "peak_vram_gb": 12.34}
    patch_everywhere("gpu_info", lambda: gpu, *MODULES)
    cfg = make_cfg()
    job_id, key = new_key()
    upload_clip(aws, key, clip_path)
    handle_record(aws.bucket, key, s3=aws.s3, cfg=cfg, roi_masks=roi_masks_small)
    result = read_result(cfg, job_id)
    assert set(result) == SECTION_1_KEYS | {"gpu"}
    assert result["gpu"] == gpu


def test_fake_inference_key_is_absent_when_fake_mode_is_off(
    aws, make_cfg, roi_masks_small, new_key, clip_path, patch_everywhere, monkeypatch
):
    """fake_inference is present (and true) only in fake mode. The real model is replaced by a
    stand-in that returns random numbers, so this runs with no torch."""
    import numpy as np

    def stand_in(path):
        rows = math.ceil(inference.probe_duration(path))
        return np.random.default_rng(0).random((rows, 20484))

    patch_everywhere("run_inference", stand_in, *MODULES)
    monkeypatch.delenv("FAKE_INFERENCE")
    cfg = make_cfg()
    job_id, key = new_key()
    upload_clip(aws, key, clip_path)
    outcome = handle_record(aws.bucket, key, s3=aws.s3, cfg=cfg, roi_masks=roi_masks_small)
    assert outcome is Outcome.DONE
    result = read_result(cfg, job_id)
    assert "fake_inference" not in result
    assert set(result) == SECTION_1_KEYS - {"fake_inference"}


# ---------------------------------------------------------------- handle_record: REJECTED


def test_oversize_object_is_rejected_deleted_and_never_downloaded(
    aws, make_cfg, roi_masks_small, new_key, spy_s3, patch_everywhere, calls
):
    cfg = make_cfg(max_upload_bytes=1000)
    job_id, key = new_key()
    upload(aws, key, b"x" * 1001)
    never_inference(patch_everywhere)
    s3 = spy_s3(forbid=("download_file", "download_fileobj", "get_object"))

    outcome = handle_record(aws.bucket, key, s3=s3, cfg=cfg, roi_masks=roi_masks_small)

    assert outcome is Outcome.REJECTED
    assert "head_object" in s3.names()
    assert "download_file" not in s3.names()
    assert not object_exists(aws, key)
    assert not (Path(cfg["paths"]["output"]) / f"{job_id}.json").exists()


def test_oversize_rejection_never_runs_ffprobe_or_inference(
    aws, make_cfg, roi_masks_small, new_key, calls
):
    cfg = make_cfg(max_upload_bytes=1000)
    _, key = new_key()
    upload(aws, key, b"x" * 5000)
    outcome = handle_record(aws.bucket, key, s3=aws.s3, cfg=cfg, roi_masks=roi_masks_small)
    assert outcome is Outcome.REJECTED
    assert calls["probe_duration"] == []
    assert calls["run_inference"] == []
    assert calls["strip_audio"] == []


def test_over_long_video_is_rejected_without_inference(
    aws, make_cfg, roi_masks_small, new_key, clip_path, calls
):
    cfg = make_cfg(max_video_duration_seconds=1)  # the clip is about 3 s
    job_id, key = new_key()
    upload_clip(aws, key, clip_path)

    outcome = handle_record(aws.bucket, key, s3=aws.s3, cfg=cfg, roi_masks=roi_masks_small)

    assert outcome is Outcome.REJECTED
    assert calls["run_inference"] == []
    assert calls["strip_audio"] == []
    assert not object_exists(aws, key)
    assert not (Path(cfg["paths"]["output"]) / f"{job_id}.json").exists()


def test_duration_check_uses_probe_duration_of_the_downloaded_file(
    aws, make_cfg, roi_masks_small, new_key, patch_everywhere
):
    """The authoritative check is ffprobe on the worker, whatever the client claimed."""
    seen = []

    def long(path):
        seen.append(Path(path).read_bytes())
        return 500.0

    patch_everywhere("probe_duration", long, *MODULES)
    never_inference(patch_everywhere)
    cfg = make_cfg()  # max 120 s
    _, key = new_key()
    upload(aws, key, b"these bytes are the object")
    outcome = handle_record(aws.bucket, key, s3=aws.s3, cfg=cfg, roi_masks=roi_masks_small)
    assert outcome is Outcome.REJECTED
    assert seen == [b"these bytes are the object"]
    assert not object_exists(aws, key)


# ---------------------------------------------------------------- handle_record: failures raise


def test_a_failing_inference_raises_instead_of_returning_an_outcome(
    aws, make_cfg, roi_masks_small, new_key, clip_path, patch_everywhere
):
    def boom(path):
        raise RuntimeError("GPU exploded")

    patch_everywhere("run_inference", boom, *MODULES)
    cfg = make_cfg()
    job_id, key = new_key()
    upload_clip(aws, key, clip_path)
    with pytest.raises(RuntimeError, match="GPU exploded"):
        handle_record(aws.bucket, key, s3=aws.s3, cfg=cfg, roi_masks=roi_masks_small)
    assert object_exists(aws, key)  # a failure is not a rejection: nothing is deleted
    assert not (Path(cfg["paths"]["output"]) / f"{job_id}.json").exists()


def test_a_missing_object_raises(aws, make_cfg, roi_masks_small, new_key):
    _, key = new_key()
    with pytest.raises(Exception):  # noqa: B017 - any failure, the exact type is not specified
        handle_record(aws.bucket, key, s3=aws.s3, cfg=make_cfg(), roi_masks=roi_masks_small)


# ---------------------------------------------------------------- process_message


@pytest.fixture
def fake_handle_record(monkeypatch):
    """Replace handle_record with a scripted one. The signature is the spec's, keyword-only."""
    seen = []

    def install(script):
        def fake(bucket, key, *, s3, cfg, roi_masks):
            seen.append((bucket, key, s3, cfg, roi_masks))
            result = script[key]
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(worker, "handle_record", fake)
        return seen

    return install


def test_zero_record_message_is_deleted_without_calling_handle_record(
    aws, make_cfg, roi_masks_small, queue_message, remaining, fake_handle_record
):
    seen = fake_handle_record({})
    body = json.dumps({"Service": "Amazon S3", "Event": "s3:TestEvent", "Bucket": aws.bucket})
    message = queue_message(body)
    process_message(message, s3=aws.s3, sqs=aws.sqs, cfg=make_cfg(), roi_masks=roi_masks_small)
    assert seen == []
    assert remaining() == []


def test_message_with_only_non_upload_keys_is_deleted(
    aws, make_cfg, roi_masks_small, queue_message, remaining, fake_handle_record
):
    seen = fake_handle_record({})
    message = queue_message(s3_event((aws.bucket, "results/x.json"), (aws.bucket, "status/y")))
    process_message(message, s3=aws.s3, sqs=aws.sqs, cfg=make_cfg(), roi_masks=roi_masks_small)
    assert seen == []
    assert remaining() == []


def test_every_record_is_handled_and_message_deleted(
    aws, make_cfg, roi_masks_small, queue_message, remaining, fake_handle_record
):
    k1, k2, k3 = (f"uploads/placeholder-user/{n}.mp4" for n in ("a", "b", "c"))
    seen = fake_handle_record({k1: Outcome.DONE, k2: Outcome.REJECTED, k3: Outcome.DONE})
    cfg = make_cfg()
    body = s3_event(
        (aws.bucket, k1), (aws.bucket, k2), (aws.bucket, "results/z.json"), (aws.bucket, k3)
    )
    process_message(queue_message(body), s3=aws.s3, sqs=aws.sqs, cfg=cfg, roi_masks=roi_masks_small)
    assert [(b, k) for b, k, *_ in seen] == [(aws.bucket, k1), (aws.bucket, k2), (aws.bucket, k3)]
    for _, _, s3, got_cfg, masks in seen:
        assert s3 is aws.s3 and got_cfg is cfg and masks is roi_masks_small
    assert remaining() == []


@pytest.mark.parametrize("raising_position", [0, 1, 2])
def test_message_is_left_when_any_record_raises(
    aws, make_cfg, roi_masks_small, queue_message, remaining, fake_handle_record, raising_position
):
    keys = [f"uploads/placeholder-user/{n}.mp4" for n in ("a", "b", "c")]
    script = {k: Outcome.DONE for k in keys}
    script[keys[raising_position]] = RuntimeError("boom")
    fake_handle_record(script)
    body = s3_event(*[(aws.bucket, k) for k in keys])
    # process_message logs the traceback; it does not let the exception escape
    process_message(
        queue_message(body), s3=aws.s3, sqs=aws.sqs, cfg=make_cfg(), roi_masks=roi_masks_small
    )
    assert len(remaining()) == 1


def test_raising_record_is_logged_with_traceback(
    aws, make_cfg, roi_masks_small, queue_message, fake_handle_record, caplog
):
    key = "uploads/placeholder-user/a.mp4"
    fake_handle_record({key: RuntimeError("distinctive failure text")})
    with caplog.at_level("DEBUG"):
        process_message(
            queue_message(s3_event((aws.bucket, key))),
            s3=aws.s3,
            sqs=aws.sqs,
            cfg=make_cfg(),
            roi_masks=roi_masks_small,
        )
    errors = [r for r in caplog.records if r.exc_info]
    assert errors, "the traceback was not logged"
    assert any("distinctive failure text" in str(r.exc_info[1]) for r in errors)


# ---------------------------------------------------------------- process_message end to end


def test_valid_upload_end_to_end_deletes_message_and_writes_result(
    aws, make_cfg, roi_masks_small, new_key, clip_path, queue_message, remaining
):
    cfg = make_cfg()
    job_id, key = new_key()
    upload_clip(aws, key, clip_path)
    process_message(
        queue_message(s3_event((aws.bucket, key))),
        s3=aws.s3,
        sqs=aws.sqs,
        cfg=cfg,
        roi_masks=roi_masks_small,
    )
    assert remaining() == []
    assert read_result(cfg, job_id)["job_id"] == job_id


def test_oversize_upload_end_to_end_deletes_object_and_message(
    aws, make_cfg, roi_masks_small, new_key, queue_message, remaining, spy_s3, patch_everywhere
):
    cfg = make_cfg(max_upload_bytes=1000)
    _, key = new_key()
    upload(aws, key, b"y" * 4000)
    never_inference(patch_everywhere)
    s3 = spy_s3(forbid=("download_file", "download_fileobj", "get_object"))
    process_message(
        queue_message(s3_event((aws.bucket, key))),
        s3=s3,
        sqs=aws.sqs,
        cfg=cfg,
        roi_masks=roi_masks_small,
    )
    assert not object_exists(aws, key)
    assert remaining() == []
    assert "download_file" not in s3.names()


def test_failing_upload_end_to_end_leaves_the_message(
    aws, make_cfg, roi_masks_small, new_key, clip_path, queue_message, remaining, patch_everywhere
):
    def boom(path):
        raise RuntimeError("model crashed")

    patch_everywhere("run_inference", boom, *MODULES)
    _, key = new_key()
    upload_clip(aws, key, clip_path)
    process_message(
        queue_message(s3_event((aws.bucket, key))),
        s3=aws.s3,
        sqs=aws.sqs,
        cfg=make_cfg(),
        roi_masks=roi_masks_small,
    )
    assert len(remaining()) == 1
    assert object_exists(aws, key)
