"""Tests for neurolens.web.app via create_app(). From docs/M0_spec.md."""

import json
from pathlib import Path

import pytest
from neurolens.web.app import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"


@pytest.fixture
def client():
    app = create_app(data_dir=DATA_DIR)
    app.config["TESTING"] = True
    return app.test_client()


def test_index_serves_index_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.data == (REPO_ROOT / "static" / "index.html").read_bytes()
    resp.close()


def test_samples_equals_samples_json(client):
    resp = client.get("/api/samples")
    assert resp.status_code == 200
    assert resp.get_json() == json.loads((DATA_DIR / "samples.json").read_text())


def test_thumbnail_is_served(client):
    samples = json.loads((DATA_DIR / "samples.json").read_text())["samples"]
    thumb = next(s["thumbnail"] for s in samples if s.get("thumbnail"))
    assert thumb.startswith("output/")
    expected = (DATA_DIR / thumb).read_bytes()
    resp = client.get(f"/data/{thumb}")
    assert resp.status_code == 200
    assert resp.data == expected
    resp.close()


def test_path_traversal_to_config_json_returns_404(client):
    for url in (
        "/data/../config.json",
        "/data/%2e%2e/config.json",
        "/data/..%2fconfig.json",
        "/data/output/../../config.json",
    ):
        assert client.get(url).status_code == 404, url


def test_path_traversal_cannot_read_file_outside_data_dir(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "ok.txt").write_text("fine")
    (tmp_path / "config.json").write_text('{"hf_token": "secret"}')
    app = create_app(data_dir=data)
    test_client = app.test_client()
    assert test_client.get("/data/ok.txt").data == b"fine"
    for url in ("/data/../config.json", "/data/%2e%2e/config.json", "/data/..%2fconfig.json"):
        resp = test_client.get(url)
        assert resp.status_code == 404, url
        assert b"secret" not in resp.data
