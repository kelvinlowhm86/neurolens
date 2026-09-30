"""Tests for GET /api/limits (Flask test client). docs/M1_spec.md 4 and 6a."""

import pytest
from neurolens.web.app import create_app


@pytest.fixture
def get_limits(tmp_path):
    def get(**kwargs):
        app = create_app(data_dir=tmp_path / "data", **kwargs)
        app.config["TESTING"] = True
        return app.test_client().get("/api/limits")

    return get


def test_returns_config_values_with_exactly_two_keys(get_limits, make_cfg):
    resp = get_limits(cfg=make_cfg())
    assert resp.status_code == 200
    assert resp.is_json
    assert resp.get_json() == {
        "max_video_duration_seconds": 120,
        "max_upload_bytes": 300000000,
    }


def test_changing_the_config_changes_the_answer(get_limits, make_cfg):
    resp = get_limits(cfg=make_cfg(max_video_duration_seconds=45, max_upload_bytes=5000))
    assert resp.status_code == 200
    assert resp.get_json() == {"max_video_duration_seconds": 45, "max_upload_bytes": 5000}


def test_upload_bytes_is_null_when_config_has_none(get_limits, make_cfg):
    cfg = make_cfg()
    del cfg["max_upload_bytes"]
    resp = get_limits(cfg=cfg)
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data) == {"max_video_duration_seconds", "max_upload_bytes"}
    assert data["max_upload_bytes"] is None
    assert data["max_video_duration_seconds"] == 120


def test_no_cfg_uses_default_duration_and_null_bytes(get_limits):
    resp = get_limits()
    assert resp.status_code == 200
    assert resp.get_json() == {"max_video_duration_seconds": 120, "max_upload_bytes": None}


def test_needs_no_aws_block(get_limits, make_cfg):
    resp = get_limits(cfg=make_cfg(with_aws=False))
    assert resp.status_code == 200
    assert resp.get_json() == {
        "max_video_duration_seconds": 120,
        "max_upload_bytes": 300000000,
    }
