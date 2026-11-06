"""M3e web pause check (docs/M3e_spec.md §8, §11 "Web").

The presign route reads the SageMaker endpoint's scalable target through Application Auto
Scaling. A target maximum of 0, or no target at all, means processing is paused; an unreadable
target fails closed. Every refusal reserves nothing.
"""

import boto3
import pytest
from botocore.exceptions import ClientError
from conftest import sign_in
from neurolens import pricing

ENDPOINT = "neurolens-gpu-test"
PRESIGN = {
    "filename": "ad.mp4",
    "content_type": "video/mp4",
    "client_duration_seconds": 27.4,
    "client_declared_bytes": 52428800,
}


class FakeAppScaling:
    def __init__(self, max_capacity=1, *, targets=None, error=None):
        self.max_capacity = max_capacity
        self.targets = targets
        self.error = error
        self.calls = []

    def describe_scalable_targets(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.targets is not None:
            return {"ScalableTargets": self.targets}
        return {"ScalableTargets": [{"MinCapacity": 0, "MaxCapacity": self.max_capacity}]}


@pytest.fixture
def signed_in_with(aws, db, cognito_cfg, cognito_app, idp, monkeypatch):
    def make(appscaling):
        cfg = cognito_cfg()
        cfg["aws"]["gpu_endpoint"] = ENDPOINT
        real_client = boto3.client

        def client(service_name, *args, **kwargs):
            if service_name == "application-autoscaling":
                return appscaling
            return real_client(service_name, *args, **kwargs)

        monkeypatch.setattr(boto3, "client", client)
        test_client = cognito_app(cfg=cfg).test_client()
        sign_in(test_client, idp, email="ann@example.com")
        return test_client

    return make


def test_presign_is_503_processing_paused_when_the_target_max_is_zero(signed_in_with, pg):
    client = signed_in_with(FakeAppScaling(max_capacity=0))
    before = pg.snapshot()

    resp = client.post("/api/uploads/presign", json=PRESIGN)

    assert resp.status_code == 503
    assert resp.get_json()["error"] == "processing_paused"
    assert "fields" not in resp.get_json()
    assert pg.snapshot() == before


def test_presign_proceeds_when_the_target_max_is_one(signed_in_with, pg):
    client = signed_in_with(FakeAppScaling(max_capacity=1))

    resp = client.post("/api/uploads/presign", json=PRESIGN)

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["reserved_cents"] == pricing.estimate_cost_cents(PRESIGN["client_duration_seconds"])
    assert pg.job(data["job_id"])["status"] == "queued"


def test_presign_reads_the_configured_endpoints_variant(signed_in_with):
    appscaling = FakeAppScaling(max_capacity=1)
    client = signed_in_with(appscaling)

    client.post("/api/uploads/presign", json=PRESIGN)

    call = appscaling.calls[-1]
    assert call["ServiceNamespace"] == "sagemaker"
    assert call["ResourceIds"] == [f"endpoint/{ENDPOINT}/variant/main"]
    assert call["ScalableDimension"] == "sagemaker:variant:DesiredInstanceCount"


def test_presign_is_503_when_there_is_no_scalable_target(signed_in_with, pg):
    client = signed_in_with(FakeAppScaling(targets=[]))
    before = pg.snapshot()

    resp = client.post("/api/uploads/presign", json=PRESIGN)

    assert resp.status_code == 503
    assert resp.get_json()["error"] == "processing_paused"
    assert pg.snapshot() == before


def test_presign_fails_closed_when_the_target_cannot_be_read(signed_in_with, pg):
    error = ClientError(
        {"Error": {"Code": "Throttling", "Message": "Rate exceeded"}},
        "DescribeScalableTargets",
    )
    client = signed_in_with(FakeAppScaling(error=error))
    before = pg.snapshot()

    resp = client.post("/api/uploads/presign", json=PRESIGN)

    assert resp.status_code >= 500
    assert resp.get_json()["error"] == "presign_failed"
    assert "fields" not in resp.get_json()
    assert pg.snapshot() == before
