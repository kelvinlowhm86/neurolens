"""Web API against a real Postgres, with fake S3 / Auto Scaling and a stubbed Stripe SDK."""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import stripe

from neurolens_common import jobs, ledger
from neurolens_common.config import Settings
from neurolens_common.testing import assert_ledger_consistent
from neurolens_web import create_app, warm_pool
from neurolens_web.services import Services, Storage

ROOT = Path(__file__).resolve().parents[2]


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, dict] = {}

    def presigned_upload(self, key, content_type, expires_s=900):
        return {"url": "http://s3.test/bucket", "fields": {"key": key, "Content-Type": content_type}}

    def presigned_get(self, key, expires_s=3600):
        return f"http://s3.test/bucket/{key}?signed"

    def read_json(self, key):
        return self.objects[key]

    def read_bytes(self, key):
        return self.objects[key]


class FakeASG:
    def __init__(self, desired=0):
        self.desired = desired
        self.calls = []

    def describe_auto_scaling_groups(self, AutoScalingGroupNames):
        return {"AutoScalingGroups": [{"DesiredCapacity": self.desired, "Instances": []}]}

    def set_desired_capacity(self, AutoScalingGroupName, DesiredCapacity, HonorCooldown):
        self.calls.append(DesiredCapacity)
        self.desired = DesiredCapacity


@pytest.fixture
def cfg(db_url):
    return Settings(database_url=db_url, dev_login_enabled=True, static_dir=ROOT / "web" / "static",
                    samples_json=ROOT / "data" / "samples.json", data_dir=ROOT / "data",
                    stripe_secret_key="sk_test_x", stripe_webhook_secret="whsec_x", loadtest_token="lt-secret")


@pytest.fixture
def storage():
    return FakeStorage()


@pytest.fixture
def client(cfg, storage, conn):
    app = create_app(cfg, Services(storage=storage), run_migrations=False)
    app.testing = True
    return app.test_client()


def login(client, email="a@test.local"):
    r = client.post("/auth/dev-login", json={"email": email})
    assert r.status_code == 200
    return r.get_json()["user_id"]


def new_job(client, **over):
    body = {"filename": "ad.mp4", "content_type": "video/mp4", "size_bytes": 5_000_000, "est_duration_s": 29.4}
    body.update(over)
    return client.post("/api/jobs", json=body)


def test_health_index_static(client):
    assert client.get("/healthz").get_json() == {"ok": True}
    assert b"NeuroLens" in client.get("/").data
    assert client.get("/static/app.js").status_code == 200


def test_signup_bonus_once(client):
    assert client.get("/api/me").status_code == 401
    login(client)
    login(client)                                                   # second sign-in: no second bonus
    me = client.get("/api/me").get_json()
    assert me["user"]["credits_available"] == 60
    assert me["pricing"]["max_video_seconds"] == 120


def test_create_job_reserves_and_returns_upload(client, conn):
    uid = login(client)
    r = new_job(client)
    assert r.status_code == 201
    body = r.get_json()
    assert body["credits_reserved"] == 30 and body["credits_available"] == 30
    assert body["upload"]["fields"]["key"] == f"uploads/{uid}/{body['job_id']}/input.mp4"
    assert body["prewarm"] == "dev_noop"
    s = client.get(f"/api/jobs/{body['job_id']}").get_json()
    assert s["status"] == "AWAITING_UPLOAD" and s["queue_position"] == 0
    assert_ledger_consistent(conn, uid)


@pytest.mark.parametrize("over,status,code", [
    ({"content_type": "video/x-msvideo"}, 400, "UNSUPPORTED_TYPE"),
    ({"est_duration_s": 121}, 400, "TOO_LONG"),
    ({"size_bytes": 300 * 1024 * 1024}, 413, "TOO_LARGE"),
    ({"est_duration_s": 0}, 400, "INVALID_REQUEST"),
])
def test_create_job_validation(client, over, status, code):
    login(client)
    r = new_job(client, **over)
    assert r.status_code == status and r.get_json()["error"]["code"] == code


def test_insufficient_credits_creates_nothing(client, conn):
    login(client)
    r = new_job(client, est_duration_s=90)
    assert r.status_code == 402
    assert r.get_json()["error"]["needed"] == 90 and r.get_json()["error"]["available"] == 60
    assert conn.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 0


def test_active_job_cap(client, conn):
    uid = login(client)
    ledger.grant(conn, uid, 1000, "GRANT", "topup")
    for _ in range(3):
        assert new_job(client).status_code == 201
    assert new_job(client).get_json()["error"]["code"] == "TOO_MANY_ACTIVE"


def test_jobs_are_private(client):
    login(client, "a@test.local")
    job_id = new_job(client).get_json()["job_id"]
    login(client, "b@test.local")
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert client.delete(f"/api/jobs/{job_id}").status_code == 404
    assert client.get("/api/jobs/not-a-uuid").status_code == 404


def test_uploaded_then_cancel_refused_and_cancel_refunds(client, conn):
    login(client)
    a = new_job(client).get_json()["job_id"]
    assert client.post(f"/api/jobs/{a}/uploaded").status_code == 200
    assert client.get(f"/api/jobs/{a}").get_json()["status"] == "UPLOADED"
    assert client.delete(f"/api/jobs/{a}").status_code == 409
    b = new_job(client, est_duration_s=10).get_json()["job_id"]
    assert client.delete(f"/api/jobs/{b}").status_code == 200
    assert client.get("/api/me").get_json()["user"]["credits_available"] == 30


def test_result_csv_history_and_render_metric(client, conn, storage):
    uid = login(client)
    job_id = new_job(client).get_json()["job_id"]
    jobs.claim_job(conn, job_id, "i-test", datetime.now(timezone.utc))
    storage.objects["results/k.json"] = {
        "filename": "ad.mp4", "duration_seconds": 2,
        "timesteps": [{"t": t, "engagement_overall": 0.5, "auditory_with_audio": 0.4, "auditory_without_audio": 0.2,
                       "regions": {r: 0.1 for r in ("ffa_faces", "eba_bodies", "ppa_scenes", "sts_social", "auditory")}}
                      for t in range(2)],
        "raw": {r: [0.01, 0.02] for r in ("ffa_faces", "eba_bodies", "ppa_scenes", "sts_social", "auditory")},
    }
    assert client.get(f"/api/jobs/{job_id}/result").status_code == 409            # not finished yet
    jobs.complete_job(conn, job_id, "i-test", credits=29, result_key="results/k.json", summary={"peak_t": 1},
                      gpu_device="L40S", peak_vram_gb=30.1, cold_start=True)

    r = client.get(f"/api/jobs/{job_id}/result").get_json()
    assert r["result"]["duration_seconds"] == 2 and r["video_url"].endswith("input.mp4?signed")
    csv_text = client.get(f"/api/jobs/{job_id}/export.csv").data.decode()
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("t,engagement_overall,ffa_faces") and len(lines) == 3
    assert client.post(f"/api/jobs/{job_id}/metrics", json={"client_render_ms": 412}).status_code == 200
    assert jobs.get_job(conn, job_id)["client_render_ms"] == 412
    hist = client.get("/api/jobs").get_json()["jobs"]
    assert hist[0]["status"] == "SUCCEEDED" and hist[0]["credits_captured"] == 29
    assert client.get("/api/me").get_json()["user"]["credits_available"] == 31
    assert_ledger_consistent(conn, uid)

    # AI report: 409 until ready, then inline PDF (or attachment with ?download=1)
    assert client.get(f"/api/jobs/{job_id}/report.pdf").status_code == 409
    storage.objects["results/report.pdf"] = b"%PDF-1.4 fake"
    jobs.set_report_status(conn, job_id, "READY", key="results/report.pdf", model="gpt-test")
    assert client.get(f"/api/jobs/{job_id}").get_json()["report"] == {"status": "READY", "error": None, "model": "gpt-test"}
    r = client.get(f"/api/jobs/{job_id}/report.pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data.startswith(b"%PDF")
    assert r.headers["Content-Disposition"].startswith("inline")
    assert client.get(f"/api/jobs/{job_id}/report.pdf?download=1").headers["Content-Disposition"].startswith("attachment")


def test_samples_local(client):
    samples = client.get("/api/samples").get_json()["samples"]
    assert samples and samples[0]["video_url"].startswith("/data/videos/")
    assert client.get("/data/../config.json").status_code == 404


# ─── Pre-warm ──────────────────────────────────────────────────────────────


def test_prewarm_scales_up_never_down(cfg, conn, make_user, make_job):
    aws_cfg = cfg.model_copy(update={"app_env": "aws", "asg_name": "nl-workers", "max_workers": 2})
    asg = FakeASG(desired=0)
    svc = Services(storage=FakeStorage(), asg=asg)
    uid = make_user(100)
    make_job(uid)
    assert warm_pool.nudge(conn, aws_cfg, svc) == "scaled_0_to_1"
    assert warm_pool.nudge(conn, aws_cfg, svc) == "already_sufficient"
    for _ in range(3):
        make_job(uid)
    assert warm_pool.nudge(conn, aws_cfg, svc) == "scaled_1_to_2"                 # capped at max_workers
    assert asg.calls == [1, 2]
    assert warm_pool.nudge(conn, aws_cfg.model_copy(update={"prewarm_enabled": False}), svc) == "disabled"


# ─── Billing ───────────────────────────────────────────────────────────────


def _webhook(client, event):
    return client.post("/api/billing/webhook", data=json.dumps(event), headers={"Stripe-Signature": "t=1,v1=x"})


def test_checkout_then_webhook_credits_once(client, conn, monkeypatch):
    uid = login(client)
    monkeypatch.setattr(stripe.checkout.Session, "create",
                        lambda **kw: SimpleNamespace(id="cs_test_123", url="https://checkout.stripe.test/x"))
    r = client.post("/api/billing/checkout", json={"pack_id": "starter"})
    assert r.get_json()["checkout_url"].startswith("https://checkout.stripe.test")
    assert client.post("/api/billing/checkout", json={"pack_id": "gold"}).status_code == 400

    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda payload, sig, secret: None)
    event = {"type": "checkout.session.completed", "data": {"object": {
        "id": "cs_test_123", "mode": "payment", "payment_status": "paid", "client_reference_id": uid,
        "metadata": {"user_id": uid, "pack_id": "starter", "credits": "300"}}}}
    assert _webhook(client, event).status_code == 200
    assert _webhook(client, event).status_code == 200                              # Stripe retries
    assert client.get("/api/me").get_json()["user"]["credits_available"] == 360
    assert conn.execute("SELECT status FROM payments WHERE stripe_session_id = 'cs_test_123'").fetchone()["status"] == "paid"
    assert_ledger_consistent(conn, uid)


def test_webhook_rejects_bad_signature(client, monkeypatch):
    def bad(payload, sig, secret):
        raise stripe.SignatureVerificationError("nope", sig)
    monkeypatch.setattr(stripe.Webhook, "construct_event", bad)
    assert _webhook(client, {"type": "checkout.session.completed"}).status_code == 400


def test_subscription_invoice_grants_monthly_credits(client, conn, monkeypatch):
    uid = login(client)
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda payload, sig, secret: None)
    _webhook(client, {"type": "checkout.session.completed", "data": {"object": {
        "id": "cs_sub", "mode": "subscription", "customer": "cus_1", "metadata": {"user_id": uid}}}})
    inv = {"type": "invoice.paid", "data": {"object": {"id": "in_1", "subscription": "sub_1", "customer": "cus_1"}}}
    _webhook(client, inv)
    _webhook(client, inv)
    assert client.get("/api/me").get_json()["user"]["credits_available"] == 60 + 1000


# ─── Auth odds and ends ────────────────────────────────────────────────────


def test_loadtest_header_auth(client):
    h = {"X-Loadtest-Token": "lt-secret", "X-Loadtest-User": "lt-7"}
    me = client.get("/api/me", headers=h).get_json()
    assert me["user"]["email"] == "lt-7@loadtest.local" and me["user"]["credits_available"] >= 10_000
    assert client.get("/api/me", headers={"X-Loadtest-Token": "wrong"}).status_code == 401


def test_google_login_unconfigured_is_clear(client):
    assert client.get("/auth/login").get_json()["error"]["code"] == "OAUTH_NOT_CONFIGURED"


def test_real_presigned_post_enforces_size_and_type(cfg, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    st = Storage.from_settings(cfg.model_copy(update={"s3_bucket": "nl-bucket"}))
    post = st.presigned_upload("uploads/u/j/input.mp4", "video/mp4")
    policy = json.loads(__import__("base64").b64decode(post["fields"]["policy"]))
    assert ["content-length-range", 1, cfg.max_upload_bytes] in policy["conditions"]
    assert {"Content-Type": "video/mp4"} in policy["conditions"]
    assert post["url"].startswith("https://nl-bucket.s3")
