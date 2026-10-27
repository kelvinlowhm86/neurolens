import json
import uuid

import pytest

from neurolens_common import pricing, s3keys
from neurolens_common.config import Settings

U, J = uuid.uuid4(), uuid.uuid4()


def test_key_round_trip():
    key = s3keys.input_key(U, J, ".MP4")
    assert key == f"uploads/{U}/{J}/input.mp4"
    assert s3keys.parse_input_key(key) == (U, J, ".mp4")


@pytest.mark.parametrize("bad", [
    f"results/{U}/{J}/result.json", f"uploads/{U}/input.mp4", f"uploads/{U}/{J}/evil.mp4",
    f"uploads/not-a-uuid/{J}/input.mp4", f"uploads/{U}/{J}/input.exe",
])
def test_parse_rejects(bad):
    with pytest.raises(ValueError):
        s3keys.parse_input_key(bad)


def _event(key: str) -> str:
    return json.dumps({"Records": [{
        "eventName": "ObjectCreated:Post", "eventTime": "2026-10-27T03:04:05.123Z",
        "s3": {"bucket": {"name": "b"}, "object": {"key": key, "size": 123}},
    }]})


def test_parse_s3_event():
    [evt] = s3keys.parse_s3_event(_event(f"uploads/{U}/{J}/input.mp4"))
    assert (evt.user_id, evt.job_id, evt.ext, evt.size) == (U, J, ".mp4", 123)
    assert evt.event_time.year == 2026 and evt.event_time.tzinfo is not None


def test_s3_test_event_and_foreign_keys_ignored():
    assert s3keys.parse_s3_event(json.dumps({"Event": "s3:TestEvent", "Service": "Amazon S3"})) == []
    assert s3keys.parse_s3_event(_event("uploads/something+else.mp4")) == []


def test_pricing():
    cfg = Settings(database_url="x")
    assert pricing.credits_for_duration(29.2, cfg) == 30
    assert pricing.credits_for_duration(2, cfg) == cfg.min_charge_credits
    assert pricing.find_pack("starter", cfg).credits == 300
    assert pricing.find_pack("nope", cfg) is None


def test_allowed_types_from_csv_env(monkeypatch):
    monkeypatch.setenv("ALLOWED_TYPES", "video/mp4, video/webm")
    assert Settings(database_url="x").allowed_types == ["video/mp4", "video/webm"]


def test_dev_login_blocked_on_aws():
    with pytest.raises(RuntimeError):
        Settings(database_url="x", app_env="aws", dev_login_enabled=True).check()
