"""Tests for neurolens.web.app via create_app(). From docs/M0_spec.md.

M3b §8 retired `/api/samples` (the page reads /data/samples.json), and with it the test that
the route equals data/samples.json.
"""

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


def test_thumbnail_is_served(tmp_path):
    """A sample thumbnail under data/output/ is served as is. Uses its own data folder, so it does
    not depend on which samples data/samples.json lists (M3d §9 empties it until the AWS day)."""
    data = tmp_path / "data"
    thumb = data / "output" / "clip" / "thumb.jpg"
    thumb.parent.mkdir(parents=True)
    thumb.write_bytes(b"\xff\xd8\xff\xe0 fake jpeg")
    test_client = create_app(data_dir=data).test_client()
    resp = test_client.get("/data/output/clip/thumb.jpg")
    assert resp.status_code == 200
    assert resp.data == thumb.read_bytes()
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
