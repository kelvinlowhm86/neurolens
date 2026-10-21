"""M3d §4, §6-§7 web routes: summary, the 410 for old results, and the brain redirect.

Flask test client + moto + PostgreSQL, with the same dev-mode fixtures as the M3b/M3c web tests.
"""

import uuid
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from conftest import STARTER_CENTS, WEB_USER_ID
from neurolens import billing, results, storage
from neurolens.web.app import create_app

SERIES = [
    "faces",
    "bodies",
    "scenes",
    "visual",
    "sound",
    "speech",
    "social",
    "see",
    "hear",
    "understand",
    "overall",
]


@pytest.fixture
def app_for(aws, db, make_cfg, tmp_path):
    def make(s3_client=None, **overrides):
        app = create_app(
            cfg=make_cfg(**overrides),
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


def reserved_job(db, *, user_id=WEB_USER_ID):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", STARTER_CENTS)
    job_id = str(uuid.uuid4())
    key = storage.object_key(user_id, job_id, "video/mp4")
    billing.reserve(db, user_id, job_id, key, "ad.mp4", 27_400)
    return job_id


def done_job(db, *, user_id=WEB_USER_ID):
    job_id = reserved_job(db, user_id=user_id)
    assert billing.claim(db, job_id) == 1
    assert billing.settle_success(db, job_id) is True
    return job_id


def refunded_job(db):
    job_id = reserved_job(db)
    assert billing.claim(db, job_id) == 1
    billing.issue_refund(db, job_id, "stalled", attempt=1)
    return job_id


def result_for(job_id, *, length=3):
    signals = {
        name: [round(0.05 * (t + 1) + index / 100, 3) for t in range(length)]
        for index, name in enumerate(SERIES)
    }
    muted = {
        name: [round(0.02 * (t + 1) + index / 100, 3) for t in range(max(1, length - 1))]
        for index, name in enumerate(SERIES)
    }
    return {
        "format": 2,
        "duration_seconds": length,
        "signals": signals,
        "muted": muted,
        "job_id": job_id,
        "processing_time_seconds": 12.3,
    }


def old_result(job_id):
    return {
        "job_id": job_id,
        "duration_seconds": 3,
        "timesteps": [{"t": 0, "engagement_overall": 0.5}],
    }


def store_result(aws, job_id, result):
    assert storage.put_result(aws.s3, aws.bucket, job_id, result) is True


def put_brain(aws, job_id, body=b"brain bytes"):
    aws.s3.put_object(Bucket=aws.bucket, Key=f"results/{job_id}.brain", Body=body)


def test_result_is_the_stored_result_plus_the_summary(client, aws, db):
    job_id = done_job(db)
    result = result_for(job_id)
    store_result(aws, job_id, result)

    resp = client.get(f"/api/jobs/{job_id}/result")

    assert resp.status_code == 200
    assert resp.get_json() == {
        **result,
        "summary": results.summarise(result),
    }


@pytest.mark.parametrize("route", ["result", "result.csv", "brain"])
def test_an_old_result_is_410_old_result_everywhere(client, aws, db, route):
    job_id = done_job(db)
    store_result(aws, job_id, old_result(job_id))
    put_brain(aws, job_id)

    resp = client.get(f"/api/jobs/{job_id}/{route}")

    assert resp.status_code == 410, route
    assert resp.get_json()["error"] == "old_result", route
    assert "older version" in resp.get_json()["message"], route


def test_the_brain_route_owns_the_job_before_any_s3_call(app_for, aws, db):
    job_id = done_job(db, user_id="someone-else")
    store_result(aws, job_id, result_for(job_id))
    put_brain(aws, job_id)
    s3 = NoCallS3()

    resp = app_for(s3_client=s3).get(f"/api/jobs/{job_id}/brain")

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"
    assert s3.calls == []


def test_the_brain_route_is_404_for_a_refunded_job(app_for, aws, db):
    job_id = refunded_job(db)
    store_result(aws, job_id, result_for(job_id))
    put_brain(aws, job_id)
    s3 = NoCallS3()

    resp = app_for(s3_client=s3).get(f"/api/jobs/{job_id}/brain")

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"
    assert s3.calls == []


def test_a_missing_brain_object_is_404_brain_missing(client, aws, db):
    job_id = done_job(db)
    store_result(aws, job_id, result_for(job_id))

    resp = client.get(f"/api/jobs/{job_id}/brain")

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "brain_missing"


def test_the_brain_route_redirects_to_a_five_minute_presigned_url(client, aws, db):
    job_id = done_job(db)
    store_result(aws, job_id, result_for(job_id))
    put_brain(aws, job_id)

    resp = client.get(f"/api/jobs/{job_id}/brain")

    assert resp.status_code == 302
    location = resp.headers["Location"]
    parsed = urlparse(location)
    assert unquote(parsed.path).endswith(f"results/{job_id}.brain")
    query = parse_qs(parsed.query)
    assert query["X-Amz-Signature"]
    assert query["X-Amz-Expires"] == ["300"]
