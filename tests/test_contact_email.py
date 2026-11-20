"""The contact address comes from NEUROLENS_CONTACT_EMAIL; blank shows the GitHub repo link."""

import pytest
from conftest import REPO_ROOT
from neurolens import settings
from neurolens.web.app import (
    CONTACT_PLACEHOLDER,
    GITHUB_REPO_URL,
    create_app,
    fill_contact_pages,
)

PAGES = ("privacy.html", "retention_guide.html")


def staged_pages(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    for page in PAGES:
        source = (REPO_ROOT / "static" / page).read_text(encoding="utf-8")
        (static / page).write_text(source, encoding="utf-8")
    return static


def make_client(aws, make_cfg, tmp_path, **overrides):
    cfg = make_cfg(**overrides)
    app = create_app(cfg=cfg, data_dir=tmp_path / "data", s3_client=aws.s3)
    app.config["TESTING"] = True
    return app.test_client()


def test_the_setting_is_stripped(monkeypatch):
    monkeypatch.setenv("NEUROLENS_CONTACT_EMAIL", "  Help@Example.com  ")
    assert settings.load_settings()["contact_email"] == "Help@Example.com"


def test_a_blank_setting_means_unset(monkeypatch):
    monkeypatch.setenv("NEUROLENS_CONTACT_EMAIL", "   ")
    assert "contact_email" not in settings.load_settings()


@pytest.mark.parametrize("page", PAGES)
def test_a_set_address_is_a_mailto_on_both_pages(aws, make_cfg, tmp_path, page):
    client = make_client(aws, make_cfg, tmp_path, contact_email="help&team@example.com")

    html = client.get(f"/static/{page}").get_data(as_text=True)

    assert CONTACT_PLACEHOLDER not in html
    assert 'href="mailto:help&amp;team@example.com"' in html
    assert "help&amp;team@example.com" in html


@pytest.mark.parametrize("page", PAGES)
def test_without_an_address_both_pages_use_the_github_fallback(aws, make_cfg, tmp_path, page):
    client = make_client(aws, make_cfg, tmp_path)

    html = client.get(f"/static/{page}").get_data(as_text=True)

    assert CONTACT_PLACEHOLDER not in html
    assert GITHUB_REPO_URL in html
    assert "GitHub repo" in html
    assert "contact the team via the" in html


def test_the_loaded_setting_reaches_the_pages(monkeypatch, aws, make_cfg, tmp_path):
    monkeypatch.setenv("NEUROLENS_CONTACT_EMAIL", "  help@example.com  ")
    cfg = settings.apply_env(make_cfg())
    app = create_app(cfg=cfg, data_dir=tmp_path / "data", s3_client=aws.s3)
    app.config["TESTING"] = True
    client = app.test_client()

    for page in PAGES:
        html = client.get(f"/static/{page}").get_data(as_text=True)
        assert 'href="mailto:help@example.com"' in html


@pytest.mark.parametrize("page", PAGES)
def test_a_staged_deploy_gets_the_address_into_the_page(tmp_path, page):
    static = staged_pages(tmp_path)

    fill_contact_pages(static, "help&team@example.com")

    text = (static / page).read_text(encoding="utf-8")
    assert CONTACT_PLACEHOLDER not in text
    assert 'href="mailto:help&amp;team@example.com"' in text


@pytest.mark.parametrize("page", PAGES)
def test_a_staged_deploy_without_an_address_uses_the_github_fallback(tmp_path, page):
    static = staged_pages(tmp_path)

    fill_contact_pages(static, "")

    text = (static / page).read_text(encoding="utf-8")
    assert CONTACT_PLACEHOLDER not in text
    assert GITHUB_REPO_URL in text


def test_a_staged_deploy_refuses_any_page_with_a_placeholder_left(tmp_path):
    static = staged_pages(tmp_path)
    (static / "new_page.html").write_text("<p>{{CONTACT_EMAIL}}</p>", encoding="utf-8")

    with pytest.raises(RuntimeError, match="new_page.html"):
        fill_contact_pages(static, "help@example.com")


def test_no_static_file_mentions_the_old_school_address():
    for path in (REPO_ROOT / "static").rglob("*"):
        if path.is_file():
            assert b"comp.nus" not in path.read_bytes(), path
