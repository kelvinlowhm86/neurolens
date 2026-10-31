"""Tests for GET /api/limits (Flask test client). docs/M1_spec.md 4 and 6a, M3d §6."""

import pytest
from neurolens import pricing
from neurolens.web.app import create_app


def _price():
    return {
        "cents_per_block": pricing.CENTS_PER_BLOCK,
        "block_seconds": pricing.BLOCK_SECONDS,
        "allowance_seconds": pricing.ALLOWANCE_SECONDS,
    }


@pytest.fixture
def get_limits(tmp_path):
    def get(**kwargs):
        app = create_app(data_dir=tmp_path / "data", **kwargs)
        app.config["TESTING"] = True
        return app.test_client().get("/api/limits")

    return get


def test_returns_the_upload_limits_and_the_colour_range(get_limits, make_cfg):
    resp = get_limits(cfg=make_cfg())
    assert resp.status_code == 200
    assert resp.is_json
    assert resp.get_json() == {
        "max_video_duration_seconds": 120,
        "max_upload_bytes": 300000000,
        "colour_range": 0.5,
        "price": _price(),
    }


def test_changing_the_config_changes_the_answer(get_limits, make_cfg):
    response = {**make_cfg()["response"], "colour_range": 0.7}
    resp = get_limits(
        cfg=make_cfg(max_video_duration_seconds=45, max_upload_bytes=5000, response=response)
    )
    assert resp.status_code == 200
    assert resp.get_json() == {
        "max_video_duration_seconds": 45,
        "max_upload_bytes": 5000,
        "colour_range": 0.7,
        "price": _price(),
    }


def test_upload_bytes_is_null_when_config_has_none(get_limits, make_cfg):
    cfg = make_cfg()
    del cfg["max_upload_bytes"]
    resp = get_limits(cfg=cfg)
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data) == {
        "max_video_duration_seconds",
        "max_upload_bytes",
        "colour_range",
        "price",
    }
    assert data["max_upload_bytes"] is None
    assert data["max_video_duration_seconds"] == 120
    assert data["colour_range"] == 0.5


def test_no_cfg_uses_default_duration_and_null_bytes_but_still_has_a_colour_range(get_limits):
    resp = get_limits()
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["max_video_duration_seconds"] == 120
    assert data["max_upload_bytes"] is None
    assert "colour_range" in data


def test_needs_no_aws_block(get_limits, make_cfg):
    resp = get_limits(cfg=make_cfg(with_aws=False))
    assert resp.status_code == 200
    assert resp.get_json() == {
        "max_video_duration_seconds": 120,
        "max_upload_bytes": 300000000,
        "colour_range": 0.5,
        "price": _price(),
    }


def test_the_price_follows_the_pricing_constant(get_limits, make_cfg, monkeypatch):
    monkeypatch.setattr(
        pricing, "CENTS_PER_BLOCK", 137
    )  # not the real price, so a typed-in copy fails
    resp = get_limits(cfg=make_cfg())
    assert resp.status_code == 200
    assert resp.get_json()["price"]["cents_per_block"] == 137
