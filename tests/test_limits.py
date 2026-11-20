"""Tests for GET /api/limits (Flask test client)."""

import pytest
from conftest import UPLOAD
from neurolens import billing, pricing, settings
from neurolens.web.app import create_app


def _price(cfg):
    return {
        "cents_per_block": settings.cents_per_block(cfg),
        "block_seconds": settings.block_seconds(cfg),
        "allowance_seconds": pricing.ALLOWANCE_SECONDS,
    }


def _limits(cfg):
    return {
        "max_video_duration_seconds": settings.max_duration(cfg),
        "max_upload_bytes": cfg.get("max_upload_bytes"),
        "colour_range": cfg["response"]["colour_range"],
        "result_retention_days": settings.result_retention_days(cfg),
        "cancel_seconds": settings.cancel_seconds(cfg),
        "invite_seconds": cfg["billing"]["invite_cents"]
        * settings.block_seconds(cfg)
        / settings.cents_per_block(cfg),
        "consent_version": billing.CONSENT_VERSION,
        "curve_min_views": cfg["research"]["curve_min_views"],
        "curve_length_tolerance_s": cfg["research"]["curve_length_tolerance_s"],
        "max_batch_files": UPLOAD["max_batch_files"],
        "price": _price(cfg),
    }


@pytest.fixture
def get_limits(tmp_path):
    def get(**kwargs):
        app = create_app(data_dir=tmp_path / "data", **kwargs)
        app.config["TESTING"] = True
        return app.test_client().get("/api/limits")

    return get


def test_returns_the_upload_limits_and_the_colour_range(get_limits, make_cfg):
    cfg = make_cfg()
    resp = get_limits(cfg=cfg)
    assert resp.status_code == 200
    assert resp.is_json
    assert resp.get_json() == _limits(cfg)


def test_changing_the_config_changes_the_answer(get_limits, make_cfg):
    response = {**make_cfg()["response"], "colour_range": 0.7}
    cfg = make_cfg(max_video_duration_seconds=45, max_upload_bytes=5000, response=response)
    resp = get_limits(cfg=cfg)
    assert resp.status_code == 200
    assert resp.get_json() == _limits(cfg)


def test_upload_bytes_is_null_when_config_has_none(get_limits, make_cfg):
    cfg = make_cfg()
    del cfg["max_upload_bytes"]
    resp = get_limits(cfg=cfg)
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data) == set(_limits(cfg))
    assert data["max_upload_bytes"] is None
    assert data["max_video_duration_seconds"] == settings.max_duration(cfg)
    assert data["colour_range"] == cfg["response"]["colour_range"]


def test_needs_no_aws_block(get_limits, make_cfg):
    cfg = make_cfg(with_aws=False)
    resp = get_limits(cfg=cfg)
    assert resp.status_code == 200
    assert resp.get_json() == _limits(cfg)


def test_the_price_follows_the_config(get_limits, make_cfg, monkeypatch):
    monkeypatch.setattr(settings, "cents_per_block", lambda cfg: 137)
    resp = get_limits(cfg=make_cfg())
    assert resp.status_code == 200
    assert resp.get_json()["price"]["cents_per_block"] == 137


def test_retention_and_cancel_windows_follow_the_config(get_limits, make_cfg):
    resp = get_limits(
        cfg=make_cfg(
            result_retention_days=45,
            queue={"cancel_seconds": 20},
        )
    )
    data = resp.get_json()
    assert data["result_retention_days"] == 45
    assert data["cancel_seconds"] == 20
