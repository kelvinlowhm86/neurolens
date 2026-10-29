"""M3h §7, §13: stored sample reports and comparisons.

The operator script copies a checked report without its usage, can add a comparison from the
canonical pair either way round, and the deploy filter only publishes comparisons whose two
samples are public. S3 and ffmpeg are faked; nothing touches AWS.
"""

import importlib.util
import io
import json
import subprocess
import sys
import uuid
from pathlib import Path

import boto3
import pytest
from neurolens import results

ROOT = Path(__file__).resolve().parent.parent
MAKE_SAMPLE = ROOT / "infra" / "make_sample.py"


class FakeS3:
    def __init__(self, objects):
        self.objects = objects
        self.downloads = []

    def download_file(self, bucket, key, target):
        self.downloads.append(key)
        if key not in self.objects:
            raise FileNotFoundError(key)
        Path(target).write_bytes(self.objects[key])

    def get_object(self, Bucket=None, Key=None, **kwargs):
        if Key not in self.objects:
            raise FileNotFoundError(Key)
        return {"Body": io.BytesIO(self.objects[Key])}


def make_result(duration=3):
    signals = {
        name: [round(0.01 * (number + 1) + 0.1 * t, 2) for t in range(duration)]
        for number, name in enumerate(results.SERIES)
    }
    muted = {name: [round(value / 2, 2) for value in signals[name]] for name in results.SERIES}
    return {
        "format": 2,
        "duration_seconds": duration,
        "signals": signals,
        "muted": muted,
    }


def report_answer(duration=3):
    return {
        "headline": "A short headline",
        "summary": "A short summary.",
        "moments": [
            {
                "start": 0,
                "end": min(1, duration - 1),
                "what_happens": "Someone enters.",
                "brain_response": "Visual areas rise.",
                "suggestion": None,
            }
        ],
        "strengths": ["Clear opening"],
        "improvements": ["Tighten the end"],
        "usage": {"input_tokens": 11, "output_tokens": 22},
    }


def comparison_answer(duration_a=3, duration_b=3):
    return {
        "headline": "A draws the stronger response",
        "summary": "A holds attention.",
        "differences": [
            {
                "a": {"start": 0, "end": min(1, duration_a - 1)},
                "b": {"start": 0, "end": min(1, duration_b - 1)},
                "what_differs": "A opens on a face.",
                "brain_response": "A's visual response rises.",
            }
        ],
        "a_does_better": ["A 0-1 s"],
        "b_does_better": ["B 2-3 s"],
        "suggestions": ["Keep A's opening."],
        "usage": {"input_tokens": 33, "output_tokens": 44},
    }


def canonical(x, y):
    return tuple(sorted(str(uuid.UUID(str(value))) for value in (x, y)))


def comparison_key(a, b):
    return f"results/{a}_{b}.report.json"


@pytest.fixture
def script(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("make_sample_under_test", MAKE_SAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = tmp_path / "data"
    (data / "videos").mkdir(parents=True)
    (data / "videos" / "clip.mp4").write_bytes(b"video")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "DATA", data)
    monkeypatch.setattr(module, "SAMPLES", data / "samples.json")
    monkeypatch.setattr(module, "bucket_name", lambda: "test-bucket")
    monkeypatch.setattr(
        module,
        "make_thumbnail",
        lambda video, target, seconds: Path(target).write_bytes(b"jpeg"),
    )
    return module, data


def run(module, monkeypatch, fake_s3, argv):
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: fake_s3)
    monkeypatch.setattr(sys, "argv", ["make_sample.py", *argv])
    module.main()


def sample_entry(sample_id, duration):
    return {
        "id": sample_id,
        "title": sample_id,
        "description": sample_id,
        "attribution": "test",
        "video_file": f"videos/{sample_id}.mp4",
        "thumbnail": f"output/{sample_id}/thumb.jpg",
        "brain_file": f"output/{sample_id}/brain",
        "result": make_result(duration),
        "summary": results.summarise(make_result(duration)),
    }


def test_make_sample_stores_the_report_without_usage(script, monkeypatch):
    module, data = script
    job_id = str(uuid.uuid4())
    result = make_result(3)
    report = report_answer(3)
    fake = FakeS3(
        {
            f"results/{job_id}.json": json.dumps(result).encode(),
            f"results/{job_id}.brain": b"brain",
            f"results/{job_id}.report.json": json.dumps(report).encode(),
        }
    )

    run(
        module,
        monkeypatch,
        fake,
        [job_id, str(data / "videos" / "clip.mp4"), "A title", "An attribution"],
    )

    document = json.loads(module.SAMPLES.read_text())
    assert len(document["samples"]) == 1
    stored = document["samples"][0]
    assert stored["id"] == "clip"
    assert stored["report"] == {key: value for key, value in report.items() if key != "usage"}
    assert "usage" not in stored["report"]


def test_make_sample_refuses_a_report_that_fails_the_check(script, monkeypatch):
    module, data = script
    job_id = str(uuid.uuid4())
    report = report_answer(3)
    report["moments"][0]["end"] = 99
    fake = FakeS3(
        {
            f"results/{job_id}.json": json.dumps(make_result(3)).encode(),
            f"results/{job_id}.brain": b"brain",
            f"results/{job_id}.report.json": json.dumps(report).encode(),
        }
    )
    module.SAMPLES.write_text(json.dumps({"samples": []}))

    with pytest.raises((SystemExit, Exception)):
        run(
            module,
            monkeypatch,
            fake,
            [job_id, str(data / "videos" / "clip.mp4"), "A title", "An attribution"],
        )

    assert json.loads(module.SAMPLES.read_text()) == {"samples": []}


def test_compare_writes_the_canonical_a_and_b_and_replaces_either_order(script, monkeypatch):
    module, data = script
    job_one, job_two = str(uuid.uuid4()), str(uuid.uuid4())
    one, two = "sample_one", "sample_two"
    duration_by_job = {job_one: 3, job_two: 5}
    samples = [sample_entry(one, 3), sample_entry(two, 5)]
    a, b = canonical(job_one, job_two)
    sample_by_job = {job_one: one, job_two: two}
    report = comparison_answer(duration_by_job[a], duration_by_job[b])
    old = {"a": sample_by_job[b], "b": sample_by_job[a], "report": {"headline": "old"}}
    module.SAMPLES.write_text(json.dumps({"samples": samples, "comparisons": [old]}))
    fake = FakeS3({comparison_key(a, b): json.dumps(report).encode()})

    run(
        module,
        monkeypatch,
        fake,
        ["compare", one, job_one, two, job_two],
    )

    document = json.loads(module.SAMPLES.read_text())
    assert document["samples"] == samples
    assert len(document["comparisons"]) == 1
    entry = document["comparisons"][0]
    assert (entry["a"], entry["b"]) == (sample_by_job[a], sample_by_job[b])
    assert entry["report"] == {key: value for key, value in report.items() if key != "usage"}


def test_deploy_filter_drops_a_comparison_unless_both_samples_are_public(tmp_path):
    script = (ROOT / "infra" / "deploy_web.sh").read_text()
    marker = "python3 -I - \"$STAGE\" <<'PY'\n"
    start = script.index(marker) + len(marker)
    end = script.index("\nPY\n", start)
    code = script[start:end]

    project = tmp_path / "project"
    data = project / "data"
    (data / "videos").mkdir(parents=True)
    (data / "videos" / "SOURCES.md").write_text(
        "| `public.mp4` | CC BY |\n| `public2.mp4` | CC BY |\n"
    )
    samples = [
        sample_entry("public", 3) | {"video_file": "videos/public.mp4"},
        sample_entry("public2", 3) | {"video_file": "videos/public2.mp4"},
        sample_entry("private", 3) | {"video_file": "videos/private.mp4"},
    ]
    comparisons = [
        {"a": "public", "b": "private", "report": {"headline": "hidden"}},
        {"a": "public", "b": "public2", "report": {"headline": "shown"}},
    ]
    (data / "samples.json").write_text(json.dumps({"samples": samples, "comparisons": comparisons}))
    for sample in samples[:2]:
        for name in (sample["video_file"], sample["thumbnail"], sample["brain_file"]):
            target = data / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"file")
    stage = tmp_path / "stage"
    (stage / "data").mkdir(parents=True)

    subprocess.run(
        [sys.executable, "-I", "-", str(stage)],
        input=code,
        text=True,
        cwd=project,
        check=True,
        capture_output=True,
    )

    deployed = json.loads((stage / "data" / "samples.json").read_text())
    assert {sample["id"] for sample in deployed["samples"]} == {"public", "public2"}
    assert deployed["comparisons"] == [
        {"a": "public", "b": "public2", "report": {"headline": "shown"}}
    ]
