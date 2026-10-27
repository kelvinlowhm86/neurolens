"""The report step with the mock LLM: moments, frames, charts and a real PDF; schema shared by providers."""

import json

import numpy as np
import pytest

from neurolens_common.config import Settings
from neurolens_worker import media, roi
from neurolens_worker.inference.mock import MockBackend
from neurolens_worker.report import build_payload, generate_report
from neurolens_worker.report.moments import pick_moments
from neurolens_worker.report.schema import REPORT_SCHEMA, ReportContent


@pytest.fixture(scope="module")
def result_and_clip(tmp_path_factory):
    d = tmp_path_factory.mktemp("report")
    clip = media.make_test_clip(d / "ad.mp4", seconds=12)
    b = MockBackend(seconds_per_pass=0)
    b.load()
    out = roi.extract(b.predict_full(clip, 12), b.predict_silent(clip, 12), b.roi_masks)
    out.update(filename="ad.mp4")
    return out, clip


def test_moments_are_spread_and_capped(result_and_clip):
    result, _ = result_and_clip
    ms = pick_moments(result, max_frames=6)
    assert 2 <= len(ms) <= 6
    ts = [m.t for m in ms]
    assert ts == sorted(ts) and all(b - a >= 2 for a, b in zip(ts, ts[1:]))
    assert [m.frame_id for m in ms] == list(range(1, len(ms) + 1))


def test_payload_carries_data_transcript_and_frames(result_and_clip):
    result, _ = result_and_clip
    ms = pick_moments(result)
    words = [{"start": 0.4, "text": "Good"}, {"start": 0.8, "text": "afternoon"}, {"start": 6.0, "text": "Next"}]
    p = build_payload(result, words, ms)
    assert len(p["per_second"]) == 12 and {"overall", "faces", "audio_on", "audio_off"} <= p["per_second"][0].keys()
    assert p["transcript"] == [{"start_s": 0.4, "text": "Good afternoon"}, {"start_s": 6.0, "text": "Next"}]
    assert [f["frame_id"] for f in p["key_frames"]] == [m.frame_id for m in ms]
    json.dumps(p)                                                   # must serialise for the LLM


def test_generate_report_with_mock_llm_makes_a_pdf(result_and_clip, tmp_path):
    result, clip = result_and_clip
    pdf, model = generate_report(Settings(database_url="x", report_llm="mock"), result, clip, [], tmp_path)
    data = pdf.read_bytes()
    assert model == "mock"
    assert data.startswith(b"%PDF") and len(data) > 20_000           # frames + charts embedded
    assert len(list((tmp_path / "frames").glob("*.jpg"))) >= 2


def test_schema_is_strict_everywhere():
    """OpenAI strict mode and Claude structured outputs both need every object closed and fully required."""
    def walk(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                walk(child)
        if node.get("type") == "array":
            walk(node["items"])
    walk(REPORT_SCHEMA)
    assert set(REPORT_SCHEMA["properties"]) == set(ReportContent.model_fields)
