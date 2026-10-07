"""M3a web changes (Flask test client + moto + PostgreSQL). Written first from docs/M3a_spec.md §2,
§6 and §10: presign with a reservation (402, the 1-second minimum, the user's key), /api/me,
Aurora-backed status and result with ownership checks made before any S3 request, the crash
window, the dev-mode guard and ensure_user once per user per process.
"""

import re
import uuid

import pytest
from conftest import STARTER_CENTS, WEB_USER_EMAIL, WEB_USER_ID
from neurolens import billing, settings, storage
from neurolens.web.app import create_app

TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
STATUS_KEYS = {
    "job_id",
    "status",
    "stage",
    "stages",
    "attempt",
    "updated_at",
    "error_code",
    "error_message",
}


def body(**overrides):
    data = {
        "filename": "ad_variant_1.mp4",
        "content_type": "video/mp4",
        "client_duration_seconds": 27.4,
        "client_declared_bytes": 52428800,
    }
    data.update(overrides)
    return data


@pytest.fixture
def app_for(aws, db, make_cfg, tmp_path):
    def make(s3_client=None, **cfg_overrides):
        app = create_app(
            cfg=make_cfg(**cfg_overrides),
            data_dir=tmp_path / "data",
            db=db,
            s3_client=s3_client or aws.s3,
        )
        app.config["TESTING"] = True
        return app.test_client()

    return make


@pytest.fixture
def client(app_for):
    return app_for()


class NoCallS3:
    """A stubbed S3 client that records (and refuses) every call."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"s3.{name} must not be called here")

        return call


def owned_job(db, user_id=WEB_USER_ID, seconds=27.4):
    """A job reserved for `user_id`, as the presign endpoint leaves it."""
    billing.ensure_user(db, user_id, f"{user_id}@example.com", STARTER_CENTS)
    job_id = str(uuid.uuid4())
    key = storage.object_key(user_id, job_id, "video/mp4")
    billing.reserve(db, user_id, job_id, key, "ad.mp4", round(seconds * 1000))
    return job_id


def put_result(aws, job_id, result=None):
    result = result or {"job_id": job_id, "duration_seconds": 3, "timesteps": [{"t": 0}]}
    assert storage.put_result(aws.s3, aws.bucket, job_id, result) is True
    return result


# ---------------------------------------------------------------- presign with reservation


def test_presign_reserves_before_handing_out_the_form(client, pg):
    resp = client.post("/api/uploads/presign", json=body())
    assert resp.status_code == 200
    data = resp.get_json()
    for name in ("job_id", "url", "fields", "object_key", "expires_in", "estimated_cost_usd"):
        assert name in data, name
    assert data["reserved_cents"] == 90
    assert data["estimated_cost_usd"] == 0.9
    assert pg.balance(WEB_USER_ID) == (410, 90)
    job = pg.job(data["job_id"])
    assert job["user_id"] == WEB_USER_ID
    assert job["status"] == "queued"
    assert job["object_key"] == data["object_key"]
    assert job["client_duration_ms"] == 27400
    assert job["filename"] == "ad_variant_1.mp4"
    assert job["reserved_cents"] == 90


def test_presign_key_contains_the_users_id(client):
    data = client.post("/api/uploads/presign", json=body(content_type="video/webm")).get_json()
    assert data["object_key"] == f"uploads/{WEB_USER_ID}/{data['job_id']}.webm"
    assert data["fields"]["key"] == data["object_key"]
    assert uuid.UUID(data["job_id"]).version == 4


def test_presign_without_enough_credit_is_402_and_reserves_nothing(app_for, pg):
    client = app_for(billing={"starter_cents": 50})
    resp = client.post("/api/uploads/presign", json=body(client_duration_seconds=27.4))
    assert resp.status_code == 402
    data = resp.get_json()
    assert data["error"] == "insufficient_credit"
    assert isinstance(data["message"], str) and data["message"]
    assert data["available_cents"] == 50
    assert data["required_cents"] == 90
    assert "fields" not in data and "url" not in data
    assert pg.balance(WEB_USER_ID) == (50, 0)
    assert pg.rows("SELECT job_id FROM jobs") == []


@pytest.mark.parametrize("seconds", [0.999, 0.5, 0])
def test_presign_rejects_a_duration_below_one_second(client, pg, seconds):
    resp = client.post("/api/uploads/presign", json=body(client_duration_seconds=seconds))
    assert resp.status_code == 400
    assert "fields" not in resp.get_json()
    assert pg.rows("SELECT job_id FROM jobs") == []


def test_presign_accepts_exactly_one_second(client, pg):
    resp = client.post("/api/uploads/presign", json=body(client_duration_seconds=1))
    assert resp.status_code == 200
    assert pg.job(resp.get_json()["job_id"])["client_duration_ms"] == 1000


def test_presign_rounds_the_client_duration_to_milliseconds(client, pg):
    data = client.post(
        "/api/uploads/presign", json=body(client_duration_seconds=12.3456)
    ).get_json()
    assert pg.job(data["job_id"])["client_duration_ms"] == 12346


def test_presign_failing_after_the_reservation_refunds_it(client, pg, patch_everywhere):
    def boom(*a, **kw):
        raise RuntimeError("S3 is down")

    patch_everywhere("presign_upload", boom, "neurolens.storage", "neurolens.web.app")
    resp = client.post("/api/uploads/presign", json=body())
    assert resp.status_code == 500
    assert resp.get_json()["error"] == "presign_failed"
    assert pg.balance(WEB_USER_ID) == (STARTER_CENTS, 0)
    [job] = pg.rows("SELECT job_id, status, error_code FROM jobs")
    assert (job["status"], job["error_code"]) == ("failed", "presign_failed")


# ---------------------------------------------------------------- /api/me


def test_me_returns_email_and_balance(client):
    resp = client.get("/api/me")
    assert resp.status_code == 200
    assert resp.get_json() == {
        "email": WEB_USER_EMAIL,
        "available_cents": STARTER_CENTS,
        "reserved_cents": 0,
    }


def test_me_shows_a_reservation(client):
    client.post("/api/uploads/presign", json=body())
    assert client.get("/api/me").get_json()["reserved_cents"] == 90


def test_me_uses_create_apps_built_in_dev_user_without_a_cfg(aws, db, tmp_path):
    app = create_app(data_dir=tmp_path / "data", db=db, s3_client=aws.s3)
    resp = app.test_client().get("/api/me")
    assert resp.status_code == 200
    assert resp.get_json() == {
        "email": "dev@localhost",
        "available_cents": 500,
        "reserved_cents": 0,
    }


def test_routes_without_a_database_return_not_configured(aws, make_cfg, tmp_path):
    app = create_app(cfg=make_cfg(), data_dir=tmp_path / "data", s3_client=aws.s3)
    client = app.test_client()
    job_id = str(uuid.uuid4())
    for resp in (
        client.get("/api/me"),
        client.post("/api/uploads/presign", json=body()),
        client.get(f"/api/jobs/{job_id}/status"),
        client.get(f"/api/jobs/{job_id}/result"),
    ):
        assert resp.status_code == 500
        assert resp.get_json()["error"] == "not_configured"


# ---------------------------------------------------------------- ensure_user once per process


def test_ensure_user_runs_once_per_user_per_process(client, db, patch_everywhere):
    calls = []
    real = billing.ensure_user

    def spy(*args, **kwargs):
        calls.append(args[1:3])
        return real(*args, **kwargs)

    patch_everywhere("ensure_user", spy, "neurolens.billing", "neurolens.web.app")
    client.get("/api/me")
    data = client.post("/api/uploads/presign", json=body()).get_json()
    client.get("/api/me")
    client.get(f"/api/jobs/{data['job_id']}/status")
    client.get(f"/api/jobs/{data['job_id']}/result")
    assert calls == [(WEB_USER_ID, WEB_USER_EMAIL)]


# ---------------------------------------------------------------- dev-mode guard


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20"])
def test_dev_mode_on_a_non_local_host_refuses_to_start(make_cfg, tmp_path, host):
    with pytest.raises(settings.UnsafeConfigError):
        create_app(cfg=make_cfg(server={"host": host, "port": 5003}), data_dir=tmp_path / "data")


def test_dev_mode_on_127_0_0_1_starts(make_cfg, tmp_path):
    create_app(cfg=make_cfg(), data_dir=tmp_path / "data")


def test_server_host_defaults_to_127_0_0_1(make_cfg, tmp_path):
    cfg = make_cfg()
    del cfg["server"]
    create_app(cfg=cfg, data_dir=tmp_path / "data")


# ---------------------------------------------------------------- status


def test_status_of_an_owned_queued_job(client, db):
    job_id = owned_job(db)
    resp = client.get(f"/api/jobs/{job_id}/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data) == STATUS_KEYS
    assert data["job_id"] == job_id
    assert data["status"] == "queued"
    assert data["stages"] == [] and data["stage"] is None
    assert data["attempt"] == 0
    assert TIME_RE.match(data["updated_at"])
    assert data["error_code"] is None and data["error_message"] is None


def test_status_of_a_processing_job_shows_its_stages(client, db):
    from datetime import UTC, datetime

    job_id = owned_job(db)
    attempt = billing.claim(db, job_id)
    billing.set_stage(
        db, job_id, attempt, "downloading", now=datetime(2026, 10, 14, 3, 22, 10, tzinfo=UTC)
    )
    billing.set_stage(
        db, job_id, attempt, "transcribing", now=datetime(2026, 10, 14, 3, 22, 41, tzinfo=UTC)
    )
    data = client.get(f"/api/jobs/{job_id}/status").get_json()
    assert data["status"] == "processing"
    assert data["stage"] == "transcribing"
    assert data["attempt"] == 1
    assert data["stages"] == [
        {"stage": "downloading", "at": "2026-10-14T03:22:10Z"},
        {"stage": "transcribing", "at": "2026-10-14T03:22:41Z"},
    ]


def test_status_of_a_done_job_keeps_stages_and_ends_after_the_last_stage(aws, client, db):
    job_id = owned_job(db)
    attempt = billing.claim(db, job_id)
    billing.set_stage(db, job_id, attempt, "downloading")
    billing.set_stage(db, job_id, attempt, "extracting_roi")
    put_result(aws, job_id)
    billing.settle_success(db, job_id)
    data = client.get(f"/api/jobs/{job_id}/status").get_json()
    assert data["status"] == "done"
    assert [s["stage"] for s in data["stages"]] == ["downloading", "extracting_roi"]
    assert TIME_RE.match(data["updated_at"])
    assert data["updated_at"] >= data["stages"][-1]["at"]  # same format: text order is time order


def test_status_of_a_refunded_job_shows_its_reason(client, db):
    job_id = owned_job(db)
    billing.claim(db, job_id)
    billing.issue_refund(
        db, job_id, "unreadable_video", "The file is not a readable video.", attempt=1
    )
    data = client.get(f"/api/jobs/{job_id}/status").get_json()
    assert data["status"] == "failed"
    assert data["error_code"] == "unreadable_video"
    assert data["error_message"] == "The file is not a readable video."


def test_status_of_a_refunded_job_with_a_late_result_stays_failed(aws, client, db):
    job_id = owned_job(db)
    billing.claim(db, job_id)
    billing.issue_refund(db, job_id, "stalled", attempt=1)
    put_result(aws, job_id)  # a late worker wrote one anyway
    assert client.get(f"/api/jobs/{job_id}/status").get_json()["status"] == "failed"


@pytest.mark.parametrize("route", ["status", "result"])
def test_another_users_job_is_404_with_no_s3_call(app_for, aws, db, route):
    job_id = owned_job(db, user_id="someone-else")
    put_result(aws, job_id)  # it even has a result
    s3 = NoCallS3()
    client = app_for(s3_client=s3)
    resp = client.get(f"/api/jobs/{job_id}/{route}")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"
    assert s3.calls == []


@pytest.mark.parametrize("route", ["status", "result"])
def test_an_unknown_job_is_404_with_no_s3_call(app_for, route):
    s3 = NoCallS3()
    client = app_for(s3_client=s3)
    resp = client.get(f"/api/jobs/{uuid.uuid4()}/{route}")
    assert resp.status_code == 404
    assert s3.calls == []


# ---------------------------------------------------------------- result


def test_result_of_a_done_job(aws, client, db):
    job_id = owned_job(db)
    billing.claim(db, job_id)
    stored = put_result(aws, job_id, {"job_id": job_id, "timesteps": [{"t": 0, "x": 0.5}]})
    billing.settle_success(db, job_id)
    resp = client.get(f"/api/jobs/{job_id}/result")
    assert resp.status_code == 200
    assert resp.get_json() == stored


def test_result_in_the_crash_window_is_served(aws, client, db):
    job_id = owned_job(db)
    billing.claim(db, job_id)
    stored = put_result(aws, job_id)
    resp = client.get(f"/api/jobs/{job_id}/result")
    assert resp.status_code == 200
    assert resp.get_json() == stored


@pytest.mark.parametrize("claimed", [False, True], ids=["queued", "processing"])
def test_result_of_an_unfinished_job_without_a_result_is_404(client, db, claimed):
    job_id = owned_job(db)
    if claimed:
        billing.claim(db, job_id)
    resp = client.get(f"/api/jobs/{job_id}/result")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"


def test_result_of_a_failed_job_is_404_even_when_a_result_exists(aws, client, db):
    """A refunded job never serves a result, so nobody gets a refunded result for free."""
    job_id = owned_job(db)
    billing.claim(db, job_id)
    billing.issue_refund(db, job_id, "stalled", attempt=1)
    put_result(aws, job_id)
    resp = client.get(f"/api/jobs/{job_id}/result")
    assert resp.status_code == 404
