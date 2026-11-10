"""Booth QR visitor passes and admin controls."""

import hashlib
import uuid

import pytest
from conftest import sign_in
from neurolens import billing, qr
from neurolens.web.app import BOOTH_ADMIN_HASHES, create_app

ADMIN_EMAIL = "admin-booth-test@example.com"
ADMIN_HASH = hashlib.sha256(ADMIN_EMAIL.encode("utf-8")).hexdigest()


@pytest.fixture
def booth_admin_patch(monkeypatch):
    monkeypatch.setattr(
        "neurolens.web.app.BOOTH_ADMIN_HASHES",
        BOOTH_ADMIN_HASHES | {ADMIN_HASH},
    )


def make_client(aws, db, make_cfg, tmp_path, **auth_overrides):
    auth = {"mode": "dev", "dev_user_id": "user-1", "dev_email": "visitor@example.com"}
    auth.update(auth_overrides)
    cfg = make_cfg(auth=auth)
    app = create_app(cfg=cfg, data_dir=tmp_path / "data", db=db, s3_client=aws.s3)
    app.config["TESTING"] = True
    return app.test_client()


def test_qr_svg_renders_valid_svg_for_booth_url():
    url = f"https://d1example.cloudfront.net/app?booth={uuid.uuid4()}.0123456789abcdef"
    rendered = qr.svg(url)
    assert rendered.startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    assert 'd="' in rendered
    assert rendered.endswith("</svg>")


def test_non_admin_cannot_access_booth_endpoints(aws, db, make_cfg, tmp_path):
    client = make_client(aws, db, make_cfg, tmp_path)

    me = client.get("/api/me").get_json()
    assert "booth_admin" not in me

    assert client.post("/api/booth/pass", json={}).status_code == 403
    assert client.get("/api/booth/stats").status_code == 403
    assert client.post("/api/booth/passes", json={"delta": 5}).status_code == 403


def test_admin_can_create_pass_adjust_pool_and_visitor_can_redeem(
    aws, db, pg, make_cfg, tmp_path, booth_admin_patch
):
    cfg = make_cfg(auth={"mode": "dev", "dev_user_id": "admin-1", "dev_email": ADMIN_EMAIL})
    app = create_app(cfg=cfg, data_dir=tmp_path / "data", db=db, s3_client=aws.s3)
    app.config["TESTING"] = True
    admin = app.test_client()
    me = admin.get("/api/me").get_json()
    assert me["booth_admin"] is True

    created = admin.post("/api/booth/pass", json={})
    assert created.status_code == 200
    pass_data = created.get_json()
    pass_id = pass_data["pass_id"]
    token = pass_data["token"]
    assert pass_id in pass_data["url"]
    assert pass_data["qr_svg"].startswith("<svg")
    assert pass_data["stats"] == {
        "passes_claimed": 0,
        "passes_remaining": billing.BOOTH_INITIAL_PASSES,
        "videos_running": 0,
        "videos_done": 0,
        "reports_ready": 0,
        "comparisons_ready": 0,
        "days": [],
        "gpu_warm_target": 0,
        "pass_claimed": False,
    }

    # Before redemption, polling stats with pass_id shows pass_claimed=False.
    stats_before = admin.get(f"/api/booth/stats?pass_id={pass_id}").get_json()
    assert stats_before["pass_claimed"] is False

    # Visitor redeems the pass on a separate browser session against the same app.
    visitor = app.test_client()
    redeemed = visitor.post("/api/wake", json={"booth": token})
    assert redeemed.status_code == 200
    guest_id = f"guest-{pass_id}"
    assert redeemed.get_json() == {"user_id": guest_id, "booth": token}

    # Visitor is now signed in as guest-<pass_id> with 2 minutes (400 cents) of credit.
    assert billing.BOOTH_PASS_CENTS == 400
    guest_me = visitor.get("/api/me").get_json()
    assert guest_me["user_id"] == guest_id
    assert guest_me["balance"]["available_cents"] == billing.BOOTH_PASS_CENTS
    assert "booth_admin" not in guest_me

    # Re-scanning or refreshing with the same token is idempotent and does not use a second pass.
    redeemed_again = visitor.post("/api/wake", json={"booth": token})
    assert redeemed_again.status_code == 200
    assert redeemed_again.get_json() == {"user_id": guest_id, "booth": token}

    # If the visitor closed their phone tab and scans a brand-new QR code on the same phone,
    # their existing guest session is preserved and the new pass is not consumed.
    fresh_pass = admin.post("/api/booth/pass", json={}).get_json()
    rescan_different = visitor.post("/api/wake", json={"booth": fresh_pass["token"]})
    assert rescan_different.status_code == 200
    assert rescan_different.get_json() == {"user_id": guest_id, "booth": token}

    stats_after = admin.get(f"/api/booth/stats?pass_id={pass_id}").get_json()
    assert stats_after["pass_claimed"] is True
    assert stats_after["passes_claimed"] == 1
    assert stats_after["passes_remaining"] == billing.BOOTH_INITIAL_PASSES - 1
    assert len(stats_after["days"]) == 1
    assert stats_after["days"][0]["passes"] == 1

    # Admin can grant credit to an account directly from the Booth modal, and reset it to 0.
    granted = admin.post(
        "/api/booth/passes",
        json={"grant_email": ADMIN_EMAIL, "minutes": 5},
    ).get_json()
    assert granted["granted"] == {"email": ADMIN_EMAIL, "minutes": 5, "cents": 1000}
    zeroed = admin.post(
        "/api/booth/passes",
        json={"grant_email": ADMIN_EMAIL, "minutes": 0},
    ).get_json()
    assert zeroed["granted"] == {"email": ADMIN_EMAIL, "minutes": 0, "cents": 0}
    assert admin.get("/api/me").get_json()["balance"]["available_cents"] == 0

    # Admin can set GPU warm target (1, 2, or 0 for auto-scale) and gpu_guard respects it.
    warm1 = admin.post("/api/booth/passes", json={"gpu_desired": 1}).get_json()
    assert warm1["gpu_warm_target"] == 1
    warm2 = admin.post("/api/booth/passes", json={"gpu_desired": 2}).get_json()
    assert warm2["gpu_warm_target"] == 2
    auto0 = admin.post("/api/booth/passes", json={"gpu_desired": 0}).get_json()
    assert auto0["gpu_warm_target"] == 0

    # Admin can add +5, +10, and stop passes (0).
    plus5 = admin.post("/api/booth/passes", json={"delta": 5}).get_json()
    assert plus5["passes_remaining"] == billing.BOOTH_INITIAL_PASSES - 1 + 5
    plus10 = admin.post("/api/booth/passes", json={"delta": 10}).get_json()
    assert plus10["passes_remaining"] == billing.BOOTH_INITIAL_PASSES - 1 + 15
    stopped = admin.post("/api/booth/passes", json={"delta": 0}).get_json()
    assert stopped["passes_remaining"] == 0

    # Once stopped, a new pass cannot be redeemed until more passes are added.
    next_pass = admin.post("/api/booth/pass", json={}).get_json()
    blocked = app.test_client().post("/api/wake", json={"booth": next_pass["token"]})
    assert blocked.status_code == 409
    assert blocked.get_json()["error"] == "booth_limit_reached"

    # Existing visitor can still re-scan their own already-claimed pass even when remaining is 0.
    still_ok = visitor.post("/api/wake", json={"booth": token})
    assert still_ok.status_code == 200


def test_booth_guest_upload_forces_research_consent_false(
    aws, db, pg, make_cfg, tmp_path, booth_admin_patch
):
    cfg = make_cfg(auth={"mode": "dev", "dev_user_id": "admin-1", "dev_email": ADMIN_EMAIL})
    app = create_app(cfg=cfg, data_dir=tmp_path / "data", db=db, s3_client=aws.s3)
    app.config["TESTING"] = True
    admin = app.test_client()
    token = admin.post("/api/booth/pass", json={}).get_json()["token"]

    visitor = app.test_client()
    assert visitor.post("/api/wake", json={"booth": token}).status_code == 200

    presign = visitor.post(
        "/api/uploads/presign",
        json={
            "filename": "clip.mp4",
            "content_type": "video/mp4",
            "client_duration_seconds": 20.0,
            "client_declared_bytes": 5_000_000,
            "research_consent": True,
        },
    )
    assert presign.status_code == 200
    job_id = presign.get_json()["job_id"]
    job = pg.job(job_id)
    assert job["research_consent"] is False
    assert job["consent_version"] is None


def test_tampered_booth_token_is_rejected(aws, db, make_cfg, tmp_path):
    visitor = make_client(aws, db, make_cfg, tmp_path)
    bad = f"{uuid.uuid4()}.0000000000000000"
    resp = visitor.post("/api/wake", json={"booth": bad})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_booth_pass"


def test_cognito_signed_out_visitor_can_redeem_booth_pass(
    aws, db, cognito_client, idp, booth_admin_patch
):
    assert sign_in(cognito_client, idp, email=ADMIN_EMAIL).status_code in (302, 303)
    token = cognito_client.post("/api/booth/pass", json={}).get_json()["token"]
    assert cognito_client.post("/logout", json={}).status_code == 200
    assert cognito_client.get("/api/me").status_code == 401

    redeem = cognito_client.post("/api/wake", json={"booth": token})
    assert redeem.status_code == 200
    me = cognito_client.get("/api/me")
    assert me.status_code == 200
    assert me.get_json()["user_id"].startswith("guest-")


def test_gpu_warm_queues_while_updating_and_scales_down_when_idle(
    aws, db, make_cfg, tmp_path, booth_admin_patch, monkeypatch
):
    import boto3

    class FakeSageMaker:
        def __init__(self):
            self.status = "Updating"
            self.current = 0
            self.desired = 1
            self.updates = []

        def describe_endpoint(self, EndpointName):
            return {
                "EndpointStatus": self.status,
                "ProductionVariants": [
                    {
                        "VariantName": "main",
                        "CurrentInstanceCount": self.current,
                        "DesiredInstanceCount": self.desired,
                    }
                ],
            }

        def update_endpoint_weights_and_capacities(self, EndpointName, DesiredWeightsAndCapacities):
            if self.status != "InService":
                raise RuntimeError("Cannot update in-progress endpoint")
            target = DesiredWeightsAndCapacities[0]["DesiredInstanceCount"]
            self.updates.append(target)
            self.desired = target

    sm = FakeSageMaker()
    real_client = boto3.client

    def fake_client(service, *args, **kwargs):
        if service == "sagemaker":
            return sm
        return real_client(service, *args, **kwargs)

    monkeypatch.setattr(boto3, "client", fake_client)
    cfg = make_cfg(
        auth={"mode": "dev", "dev_user_id": "admin-1", "dev_email": ADMIN_EMAIL},
        aws={"s3_bucket": aws.bucket, "gpu_endpoint": "neurolens"},
    )
    app = create_app(cfg=cfg, data_dir=tmp_path / "data", db=db, s3_client=aws.s3)
    app.config["TESTING"] = True
    admin = app.test_client()

    # Clicking Keep 2 GPUs while endpoint is Updating succeeds without 409 and queues target=2.
    res = admin.post("/api/booth/passes", json={"gpu_desired": 2})
    assert res.status_code == 200
    body = res.get_json()
    assert body["gpu_warm_target"] == 2
    assert body["gpu"]["queued_desired"] == 2
    assert sm.updates == []

    # Switching to 0 (Auto-scale) while still Updating also succeeds and queues target=0.
    res0 = admin.post("/api/booth/passes", json={"gpu_desired": 0})
    assert res0.status_code == 200
    body0 = res0.get_json()
    assert body0["gpu_warm_target"] == 0
    assert body0["gpu"]["queued_desired"] == 0
    assert sm.updates == []

    # Once SageMaker finishes booting into InService (current=1, desired=1) and queue is empty,
    # the next ?gpu=1 poll automatically scales it down to 0.
    sm.status = "InService"
    sm.current = 1
    polled = admin.get("/api/booth/stats?gpu=1").get_json()
    assert sm.updates == [0]
    assert polled["gpu"]["desired"] == 0
