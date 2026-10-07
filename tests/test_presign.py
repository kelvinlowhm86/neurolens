"""Tests for POST /api/uploads/presign (Flask test client + moto). docs/M1_spec.md 4 and 4a.

Updated for docs/M3a_spec.md §6 (a spec'd change): the app gets a PostgreSQL database (the route
reserves credit first) and the key is under the development user's folder,
uploads/{user_id}/{job_id}{ext}, retiring placeholder-user. A route without an S3 client answers
500 `not_configured`. The reservation itself is tested in tests/test_m3a_web.py.
"""

import base64
import json
import re
import uuid

import pytest
from conftest import STARTER_CENTS, WEB_USER_ID
from neurolens.web.app import create_app

KEY_RE = re.compile(rf"^uploads/{WEB_USER_ID}/([0-9a-f-]{{36}})(\.[a-z0-9]+)$")


@pytest.fixture
def client_for(aws, db, make_cfg, tmp_path):
    def make(with_s3=True, **cfg_overrides):
        cfg = make_cfg(**cfg_overrides)
        app = create_app(
            data_dir=tmp_path / "data", cfg=cfg, db=db, s3_client=aws.s3 if with_s3 else None
        )
        app.config["TESTING"] = True
        return app.test_client(), cfg

    return make


@pytest.fixture
def client(client_for):
    return client_for()[0]


def body(**overrides):
    data = {
        "filename": "ad_variant_1.mp4",
        "content_type": "video/mp4",
        "client_duration_seconds": 27.4,
        "client_declared_bytes": 52428800,
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------- success


def test_success_shape(client):
    resp = client.post("/api/uploads/presign", json=body())
    assert resp.status_code == 200
    data = resp.get_json()
    for name in ("job_id", "url", "fields", "object_key", "expires_in", "estimated_cost_usd"):
        assert name in data, name
    assert isinstance(data["url"], str)
    assert isinstance(data["fields"], dict)
    assert data["expires_in"] == 300


def test_success_key_is_under_the_users_folder_and_contains_job_id(client):
    data = client.post("/api/uploads/presign", json=body()).get_json()
    match = KEY_RE.match(data["object_key"])
    assert match, data["object_key"]
    assert match.group(1) == data["job_id"]
    assert uuid.UUID(data["job_id"]).version == 4
    assert data["fields"]["key"] == data["object_key"]


def test_success_fields_hold_a_signed_policy_with_the_size_cap(client_for):
    """moto-limited: only the policy document our code builds is checked, not S3's enforcement."""
    client, cfg = client_for(max_upload_bytes=777000)
    data = client.post("/api/uploads/presign", json=body(client_declared_bytes=1000)).get_json()
    assert "policy" in data["fields"]
    conditions = json.loads(base64.b64decode(data["fields"]["policy"]))["conditions"]
    ranges = [c for c in conditions if isinstance(c, list) and c[0] == "content-length-range"]
    assert len(ranges) == 1 and ranges[0][-1] == cfg["max_upload_bytes"]


def test_success_estimated_cost_uses_the_pricing_formula(client):
    data = client.post("/api/uploads/presign", json=body(client_duration_seconds=27.4)).get_json()
    assert data["estimated_cost_usd"] == 0.9
    data = client.post("/api/uploads/presign", json=body(client_duration_seconds=61)).get_json()
    assert data["estimated_cost_usd"] == 2.7  # ceil(60.5 / 30) = 3 blocks


def test_each_request_gets_its_own_job_id(client):
    a = client.post("/api/uploads/presign", json=body()).get_json()
    b = client.post("/api/uploads/presign", json=body()).get_json()
    assert a["job_id"] != b["job_id"]
    assert a["object_key"] != b["object_key"]


def test_duration_equal_to_the_maximum_is_accepted(client):
    resp = client.post("/api/uploads/presign", json=body(client_duration_seconds=120))
    assert resp.status_code == 200
    assert resp.get_json()["estimated_cost_usd"] == 3.6


def test_declared_bytes_equal_to_the_cap_is_accepted(client_for):
    client, cfg = client_for(max_upload_bytes=5000)
    resp = client.post("/api/uploads/presign", json=body(client_declared_bytes=5000))
    assert resp.status_code == 200


def test_missing_declared_bytes_still_proceeds(client):
    payload = body()
    del payload["client_declared_bytes"]
    resp = client.post("/api/uploads/presign", json=payload)
    assert resp.status_code == 200


# ------------------------------------------- key extension follows content_type


@pytest.mark.parametrize(
    "content_type, filename, ext",
    [
        ("video/mp4", "clip.mov", ".mp4"),
        ("video/mp4", "evil.exe", ".mp4"),
        ("video/quicktime", "clip.mp4", ".mov"),
        ("video/webm", "clip.mp4", ".webm"),
        ("video/webm", "no_extension", ".webm"),
        ("video/mp4", "../../etc/passwd", ".mp4"),
    ],
)
def test_key_extension_follows_content_type_not_filename(client, content_type, filename, ext):
    resp = client.post(
        "/api/uploads/presign", json=body(content_type=content_type, filename=filename)
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["object_key"] == f"uploads/{WEB_USER_ID}/{data['job_id']}{ext}"


# ---------------------------------------------------------------- errors


@pytest.mark.parametrize("content_type", ["application/pdf", "video/x-msvideo", "", "text/plain"])
def test_unsupported_content_type(client, content_type):
    resp = client.post("/api/uploads/presign", json=body(content_type=content_type))
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "unsupported_content_type"
    assert isinstance(data["message"], str) and data["message"]


def test_duration_exceeds_max_estimated(client):
    resp = client.post("/api/uploads/presign", json=body(client_duration_seconds=120.5))
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "duration_exceeds_max_estimated"
    assert isinstance(data["message"], str) and data["message"]
    assert data["max_seconds"] == 120


def test_max_seconds_follows_the_config(client_for):
    client, _ = client_for(max_video_duration_seconds=45)
    resp = client.post("/api/uploads/presign", json=body(client_duration_seconds=46))
    assert resp.status_code == 400
    assert resp.get_json()["max_seconds"] == 45
    assert (
        client.post("/api/uploads/presign", json=body(client_duration_seconds=45)).status_code
        == 200
    )


def test_file_too_large(client_for):
    client, cfg = client_for(max_upload_bytes=5000)
    resp = client.post("/api/uploads/presign", json=body(client_declared_bytes=5001))
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "file_too_large"
    assert isinstance(data["message"], str) and data["message"]
    assert data["max_bytes"] == 5000


def test_error_responses_do_not_hand_out_upload_fields(client):
    for payload in (
        body(content_type="application/pdf"),
        body(client_duration_seconds=500),
        body(client_declared_bytes=10**12),
    ):
        data = client.post("/api/uploads/presign", json=payload).get_json()
        assert "fields" not in data and "url" not in data and "job_id" not in data


def test_no_s3_client_gives_not_configured_and_holds_no_money(client_for, pg):
    """M3a §6: a route that needs an S3 client the app doesn't have returns 500 not_configured
    (M1 said presign_failed)."""
    client, _ = client_for(with_s3=False, with_aws=False)
    resp = client.post("/api/uploads/presign", json=body())
    assert resp.status_code == 500
    data = resp.get_json()
    assert data["error"] == "not_configured"
    assert "fields" not in data and "url" not in data
    assert pg.rows("SELECT 1 FROM jobs WHERE status IN ('queued', 'processing')") == []
    assert pg.balance(WEB_USER_ID) in (None, (STARTER_CENTS, 0))


def test_boto_failure_gives_presign_failed(client, patch_everywhere):
    def boom(*args, **kwargs):
        raise RuntimeError("S3 is down")

    patch_everywhere("presign_upload", boom, "neurolens.storage", "neurolens.web.app")
    resp = client.post("/api/uploads/presign", json=body())
    assert resp.status_code == 500
    assert resp.get_json()["error"] == "presign_failed"


# ------------------------------------------------ input validation (hardening)


def post_raw(client, raw):
    return client.post("/api/uploads/presign", data=raw, content_type="application/json")


@pytest.mark.parametrize("raw", ["[1]", '"x"', "null", "5", "true"])
def test_body_that_is_not_an_object_is_invalid_request(client, raw):
    resp = post_raw(client, raw)
    assert resp.status_code == 400
    assert resp.is_json
    data = resp.get_json()
    assert data["error"] == "invalid_request"
    assert isinstance(data["message"], str) and data["message"]


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_duration_is_invalid_request(client, token):
    raw = '{"content_type": "video/mp4", "client_duration_seconds": %s}' % token
    resp = post_raw(client, raw)
    assert resp.status_code == 400
    assert resp.is_json
    assert resp.get_json()["error"] == "invalid_request"


def test_nan_declared_bytes_is_ignored_like_an_absent_one(client):
    raw = (
        '{"content_type": "video/mp4", "client_duration_seconds": 27.4,'
        ' "client_declared_bytes": NaN}'
    )
    resp = post_raw(client, raw)
    assert resp.status_code == 200
    assert "fields" in resp.get_json()


# ------------------------------------------------ missing size limit (hardening)


def test_config_without_max_upload_bytes_cannot_presign(aws, db, pg, make_cfg, tmp_path):
    cfg = make_cfg()
    del cfg["max_upload_bytes"]
    app = create_app(data_dir=tmp_path / "data", cfg=cfg, db=db, s3_client=aws.s3)
    app.config["TESTING"] = True
    client = app.test_client()
    resp = client.post("/api/uploads/presign", json=body())
    assert resp.status_code == 500
    data = resp.get_json()
    assert data["error"] == "presign_failed"
    assert "fields" not in data and "url" not in data
    # M3a: no money is left held (nothing reserved, or the reservation refunded)
    assert pg.rows("SELECT 1 FROM jobs WHERE status IN ('queued', 'processing')") == []
    assert pg.balance(WEB_USER_ID) in (None, (STARTER_CENTS, 0))
    # the limits endpoint still answers, reporting the missing limit as null
    limits = client.get("/api/limits")
    assert limits.status_code == 200
    assert limits.get_json()["max_upload_bytes"] is None


# ------------------------------------------------ content type pinned (hardening)


@pytest.mark.parametrize("content_type", ["video/mp4", "video/quicktime", "video/webm"])
def test_route_fields_pin_the_content_type(client, content_type):
    data = client.post("/api/uploads/presign", json=body(content_type=content_type)).get_json()
    assert data["fields"]["Content-Type"] == content_type
    conditions = json.loads(base64.b64decode(data["fields"]["policy"]))["conditions"]
    assert {"Content-Type": content_type} in conditions


# ---------------------------------------------------------------- create_app(cfg=...)


def test_create_app_with_cfg_needs_no_config_json(monkeypatch, tmp_path, make_cfg):
    empty_root = tmp_path / "empty_root"
    empty_root.mkdir()
    monkeypatch.setenv("NEUROLENS_ROOT", str(empty_root))
    assert not (empty_root / "config.json").exists()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    app = create_app(data_dir=data_dir, cfg=make_cfg())
    resp = app.test_client().get("/api/limits")  # M3b §8: /api/samples is removed
    assert resp.status_code == 200
    assert resp.get_json()["max_upload_bytes"] == make_cfg()["max_upload_bytes"]


def test_create_app_without_cfg_or_aws_block_still_serves_other_routes(tmp_path):
    """The M0 web tests keep working unchanged: no cfg, no aws block, no config.json needed."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "ok.txt").write_text("fine")
    app = create_app(data_dir=data_dir)
    assert app.test_client().get("/data/ok.txt").data == b"fine"
