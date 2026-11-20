"""Booth admins come from the NEUROLENS_ADMIN_EMAILS deployment setting, not from code."""

import pytest
from neurolens import settings
from neurolens.web import app as web_app
from neurolens.web.app import create_app

ADMIN_EMAIL = "admin@example.com"


def test_admin_emails_are_lowercased_stripped_and_drop_empty_entries(monkeypatch):
    monkeypatch.setenv("NEUROLENS_ADMIN_EMAILS", " Ann@Example.com , BOB@example.COM ,, ")
    cfg = settings.load_settings()
    assert cfg["auth"]["admin_emails"] == ["ann@example.com", "bob@example.com"]


def test_unset_admin_emails_setting_is_empty(monkeypatch):
    monkeypatch.delenv("NEUROLENS_ADMIN_EMAILS", raising=False)
    cfg = settings.load_settings()
    assert cfg.get("auth", {}).get("admin_emails", []) == []


def test_blank_admin_emails_setting_is_empty(monkeypatch):
    monkeypatch.setenv("NEUROLENS_ADMIN_EMAILS", "  ,  , ")
    cfg = settings.load_settings()
    assert cfg.get("auth", {}).get("admin_emails", []) == []


def make_client(aws, db, make_cfg, tmp_path, *, admin_emails=None, email="visitor@example.com"):
    auth = {"mode": "dev", "dev_user_id": "user-1", "dev_email": email}
    if admin_emails is not None:
        auth["admin_emails"] = admin_emails
    cfg = make_cfg(auth=auth)
    app = create_app(cfg=cfg, data_dir=tmp_path / "data", db=db, s3_client=aws.s3)
    app.config["TESTING"] = True
    return app.test_client()


def test_a_listed_admin_gets_the_flag_and_can_use_the_booth_routes(aws, db, make_cfg, tmp_path):
    client = make_client(
        aws,
        db,
        make_cfg,
        tmp_path,
        admin_emails=["admin@example.com"],
        email=" Admin@Example.COM ",
    )

    assert client.get("/api/me").get_json()["booth_admin"] is True
    assert client.post("/api/booth/pass", json={}).status_code == 200
    assert client.get("/api/booth/stats").status_code == 200
    assert client.post("/api/booth/passes", json={"delta": 5}).status_code == 200


def test_an_unlisted_user_gets_no_flag_and_is_forbidden(aws, db, make_cfg, tmp_path):
    client = make_client(
        aws, db, make_cfg, tmp_path, admin_emails=[ADMIN_EMAIL], email="visitor@example.com"
    )

    assert "booth_admin" not in client.get("/api/me").get_json()
    assert client.post("/api/booth/pass", json={}).status_code == 403
    assert client.get("/api/booth/stats").status_code == 403
    assert client.post("/api/booth/passes", json={"delta": 5}).status_code == 403


@pytest.mark.parametrize("admin_emails", [None, []], ids=["missing", "empty"])
def test_a_missing_or_empty_admin_setting_makes_nobody_admin(
    aws, db, make_cfg, tmp_path, admin_emails
):
    client = make_client(aws, db, make_cfg, tmp_path, admin_emails=admin_emails, email=ADMIN_EMAIL)

    assert "booth_admin" not in client.get("/api/me").get_json()
    assert client.post("/api/booth/pass", json={}).status_code == 403
    assert client.get("/api/booth/stats").status_code == 403
    assert client.post("/api/booth/passes", json={"delta": 5}).status_code == 403


def test_the_app_no_longer_hard_codes_admin_hashes():
    assert not hasattr(web_app, "BOOTH_ADMIN_HASHES")
