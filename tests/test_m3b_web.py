"""M3b web endpoints (Flask test client + moto + PostgreSQL). Written first from
docs/M3b_spec.md §2c, §4a, §8 and §11: /api/me, job history with its `limit` rule and 30-day
`result_available`, the CSV export and `neurolens.results.to_csv`, the result routes' exact
not-available errors (ownership checked before any S3 call), the crash window, presign refused
while GPU work is paused (reading the worker group, failing closed, reserving nothing), 503
`database_waking` within the web app's 45 s budget, and /api/samples gone.

History, CSV and result tests run in dev mode, which is always signed in (§2b).
"""

import csv
import io
import json
import uuid

import pytest
from conftest import STARTER_CENTS, WEB_USER_EMAIL, WEB_USER_ID, WORKER_GROUP, sign_in
from neurolens import billing, storage
from neurolens.web.app import create_app

CSV_COLUMNS = [
    "t",
    "engagement_overall",
    "ffa_faces",
    "eba_bodies",
    "ppa_scenes",
    "sts_social",
    "auditory",
    "auditory_with_audio",
    "auditory_without_audio",
]
REGIONS = ["ffa_faces", "eba_bodies", "ppa_scenes", "sts_social", "auditory"]
JOB_KEYS = {
    "job_id",
    "filename",
    "status",
    "created_at",
    "verified_duration_ms",
    "captured_cents",
    "error_code",
    "result_available",
}
DAY = 24 * 3600

# Shaped like the worker's result (engagement.extract_engagement): the last step has no
# with/without-audio values (the no-audio pass was shorter), and the numbers are awkward on
# purpose (a float that is whole, an int, tiny and long decimals).
RESULT = {
    "duration_seconds": 3,
    "timesteps": [
        {
            "t": 0,
            "engagement_overall": 0.1786,
            "regions": {
                "ffa_faces": 0.4306,
                "eba_bodies": 0.0,
                "ppa_scenes": 1.0,
                "sts_social": 0.4543,
                "auditory": 0.1023,
            },
            "auditory_with_audio": 0.1023,
            "auditory_without_audio": 0.241,
        },
        {
            "t": 1,
            "engagement_overall": 0.3423,
            "regions": {
                "ffa_faces": 1e-05,
                "eba_bodies": 0.148,
                "ppa_scenes": 0.6743,
                "sts_social": 0.4287,
                "auditory": 0,
            },
            "auditory_with_audio": 0.123456789012,
            "auditory_without_audio": 0.4022,
        },
        {
            "t": 2,
            "engagement_overall": 1.0,
            "regions": {
                "ffa_faces": 0.5,
                "eba_bodies": 0.25,
                "ppa_scenes": 0.125,
                "sts_social": 0.0625,
                "auditory": 0.9999,
            },
            "auditory_with_audio": None,
            "auditory_without_audio": None,
        },
    ],
    "job_id": "filled in per test",
    "processing_time_seconds": 12.3,
}


def expected_cells(step):
    values = [step["t"], step["engagement_overall"]]
    values += [step["regions"][name] for name in REGIONS]
    values += [step["auditory_with_audio"], step["auditory_without_audio"]]
    return ["" if v is None else json.dumps(v) for v in values]


def parse_csv(text):
    return list(csv.reader(io.StringIO(text)))


class NoCallS3:
    """A stubbed S3 client that records (and refuses) every call."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"s3.{name} must not be called here")

        return call


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


def owned_job(db, user_id=WEB_USER_ID, seconds=27.4, starter_cents=10_000):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", starter_cents)
    job_id = str(uuid.uuid4())
    key = storage.object_key(user_id, job_id, "video/mp4")
    billing.reserve(db, user_id, job_id, key, f"ad-{job_id[:8]}.mp4", round(seconds * 1000))
    return job_id


def put_result(aws, job_id):
    result = {**RESULT, "job_id": job_id}
    assert storage.put_result(aws.s3, aws.bucket, job_id, result) is True
    return result


def done_job(aws, db, **kwargs):
    job_id = owned_job(db, **kwargs)
    attempt = billing.claim(db, job_id)
    billing.verify(db, job_id, attempt, 27_400, 120)
    result = put_result(aws, job_id)
    billing.settle_success(db, job_id)
    return job_id, result


def failed_job(db, reason="unreadable_video"):
    job_id = owned_job(db)
    attempt = billing.claim(db, job_id)
    billing.issue_refund(db, job_id, reason, "The file is not a readable video.", attempt=attempt)
    return job_id


# ---------------------------------------------------------------- /api/me


def test_me_in_dev_mode(client):
    resp = client.get("/api/me")
    assert resp.status_code == 200
    assert resp.get_json() == {
        "user_id": WEB_USER_ID,
        "email": WEB_USER_EMAIL,
        "balance": {"available_cents": STARTER_CENTS, "reserved_cents": 0},
        "can_top_up": False,
    }


def test_me_shows_a_reservation(client):
    client.post(
        "/api/uploads/presign",
        json={"content_type": "video/mp4", "client_duration_seconds": 27.4, "filename": "a.mp4"},
    )
    assert client.get("/api/me").get_json()["balance"] == {
        "available_cents": STARTER_CENTS - 90,
        "reserved_cents": 90,
    }


def test_me_in_cognito_mode_after_sign_in(cognito_client, idp):
    sign_in(cognito_client, idp, email="ann@example.com")
    data = cognito_client.get("/api/me").get_json()
    assert set(data) == {"user_id", "email", "balance", "can_top_up"}
    assert uuid.UUID(data["user_id"])
    assert data["email"] == "ann@example.com"
    assert data["balance"] == {"available_cents": STARTER_CENTS, "reserved_cents": 0}
    assert data["can_top_up"] is False


def test_samples_are_no_longer_an_api_route(client):
    """§4a: the page reads /data/samples.json."""
    assert client.get("/api/samples").status_code == 404


# ---------------------------------------------------------------- history


def test_history_lists_only_the_users_jobs_newest_first(client, db, pg):
    old, middle, new = (owned_job(db) for _ in range(3))
    owned_job(db, user_id="someone-else")
    for job_id, age in ((old, 300), (middle, 200), (new, 100)):
        pg.age(job_id, created_s=age)
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    jobs = resp.get_json()
    assert [j["job_id"] for j in jobs] == [new, middle, old]
    for job in jobs:
        assert set(job) == JOB_KEYS


def test_history_rows_carry_the_jobs_values(aws, client, db):
    done_id, _ = done_job(aws, db)
    failed_id = failed_job(db)
    rows = {j["job_id"]: j for j in client.get("/api/jobs").get_json()}
    done, failed = rows[done_id], rows[failed_id]
    assert done["status"] == "done"
    assert done["filename"] == f"ad-{done_id[:8]}.mp4"
    assert done["verified_duration_ms"] == 27_400
    assert done["captured_cents"] == 90
    assert done["error_code"] is None
    assert done["created_at"]
    assert failed["status"] == "failed"
    assert failed["error_code"] == "unreadable_video"
    assert failed["captured_cents"] is None


@pytest.mark.parametrize("limit", ["1", "100"])
def test_history_accepts_a_limit_of_1_to_100(client, db, limit):
    for _ in range(2):
        owned_job(db)
    resp = client.get(f"/api/jobs?limit={limit}")
    assert resp.status_code == 200
    assert len(resp.get_json()) == min(int(limit), 2)


@pytest.mark.parametrize("limit", ["0", "101", "abc", "-1", "1.5", ""])
def test_history_refuses_any_other_limit(client, limit):
    resp = client.get(f"/api/jobs?limit={limit}")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "bad_limit"


def test_history_returns_at_most_50_by_default(client, db):
    for _ in range(51):
        owned_job(db)
    assert len(client.get("/api/jobs").get_json()) == 50


def test_result_available_only_for_a_done_job_under_30_days_old(aws, client, db, pg):
    fresh, _ = done_job(aws, db)
    almost, _ = done_job(aws, db)
    expired, _ = done_job(aws, db)
    pg.age(almost, created_s=29 * DAY)
    pg.age(expired, created_s=31 * DAY)
    queued = owned_job(db)
    failed = failed_job(db)
    rows = {j["job_id"]: j["result_available"] for j in client.get("/api/jobs").get_json()}
    assert rows == {fresh: True, almost: True, expired: False, queued: False, failed: False}


# ---------------------------------------------------------------- CSV


def test_to_csv_columns_rows_and_exact_values():
    from neurolens.results import to_csv

    rows = parse_csv(to_csv(RESULT))
    assert rows[0] == CSV_COLUMNS
    assert len(rows) == 1 + len(RESULT["timesteps"])
    for row, step in zip(rows[1:], RESULT["timesteps"], strict=True):
        assert row == expected_cells(step)


def test_to_csv_writes_none_as_an_empty_cell():
    from neurolens.results import to_csv

    last = parse_csv(to_csv(RESULT))[-1]
    assert last[-2:] == ["", ""]


def test_csv_route_serves_the_jobs_result_as_an_attachment(aws, client, db):
    job_id, result = done_job(aws, db)
    resp = client.get(f"/api/jobs/{job_id}/result.csv")
    assert resp.status_code == 200
    assert resp.headers["Content-Disposition"] == (f'attachment; filename="neurolens-{job_id}.csv"')
    rows = parse_csv(resp.get_data(as_text=True))
    assert rows[0] == CSV_COLUMNS
    assert rows[1:] == [expected_cells(step) for step in result["timesteps"]]


def test_csv_matches_the_json_result_exactly(aws, client, db):
    job_id, _ = done_job(aws, db)
    stored = client.get(f"/api/jobs/{job_id}/result").get_json()
    rows = parse_csv(client.get(f"/api/jobs/{job_id}/result.csv").get_data(as_text=True))
    assert rows[1:] == [expected_cells(step) for step in stored["timesteps"]]


# ---------------------------------------------------------------- result and CSV: when available

ROUTES = ["result", "result.csv"]


@pytest.mark.parametrize("route", ROUTES)
def test_a_processing_job_whose_result_exists_serves_it(aws, client, db, route):
    """M3a's crash window: the worker wrote the result, then died before settling."""
    job_id = owned_job(db)
    billing.claim(db, job_id)
    result = put_result(aws, job_id)
    resp = client.get(f"/api/jobs/{job_id}/{route}")
    assert resp.status_code == 200
    if route == "result":
        assert resp.get_json() == result
    else:
        rows = parse_csv(resp.get_data(as_text=True))
        assert rows[1:] == [expected_cells(step) for step in result["timesteps"]]


@pytest.mark.parametrize("route", ROUTES)
def test_a_queued_job_whose_result_exists_serves_it(aws, client, db, route):
    job_id = owned_job(db)
    put_result(aws, job_id)
    assert client.get(f"/api/jobs/{job_id}/{route}").status_code == 200


@pytest.mark.parametrize("route", ROUTES)
def test_another_users_job_is_404_not_found_with_no_s3_call(app_for, aws, db, route):
    job_id = owned_job(db, user_id="someone-else")
    put_result(aws, job_id)
    billing.claim(db, job_id)
    billing.settle_success(db, job_id)
    s3 = NoCallS3()
    resp = app_for(s3_client=s3).get(f"/api/jobs/{job_id}/{route}")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"
    assert s3.calls == []


@pytest.mark.parametrize("route", ROUTES)
def test_an_unknown_job_is_404_not_found_with_no_s3_call(app_for, route):
    s3 = NoCallS3()
    resp = app_for(s3_client=s3).get(f"/api/jobs/{uuid.uuid4()}/{route}")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"
    assert s3.calls == []


@pytest.mark.parametrize("route", ROUTES)
def test_a_failed_job_is_404_not_found_even_with_a_late_result(aws, client, db, route):
    job_id = failed_job(db)
    put_result(aws, job_id)  # a late worker wrote one anyway
    resp = client.get(f"/api/jobs/{job_id}/{route}")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("claimed", [False, True], ids=["queued", "processing"])
def test_an_unfinished_job_without_a_result_is_404_result_not_ready(client, db, route, claimed):
    job_id = owned_job(db)
    if claimed:
        billing.claim(db, job_id)
    resp = client.get(f"/api/jobs/{job_id}/{route}")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "result_not_ready"


@pytest.mark.parametrize("route", ROUTES)
def test_a_done_job_whose_result_expired_is_404_result_expired(aws, client, db, route):
    job_id, _ = done_job(aws, db)
    aws.s3.delete_object(Bucket=aws.bucket, Key=f"results/{job_id}.json")  # the 30-day expiry
    resp = client.get(f"/api/jobs/{job_id}/{route}")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "result_expired"


@pytest.mark.parametrize("route", ROUTES)
def test_a_bad_job_id_is_400(client, route):
    resp = client.get(f"/api/jobs/not-a-uuid/{route}")
    assert resp.status_code == 400


# ---------------------------------------------------------------- presign while GPU work is paused

PRESIGN = {
    "filename": "ad.mp4",
    "content_type": "video/mp4",
    "client_duration_seconds": 27.4,
    "client_declared_bytes": 52428800,
}


def worker_group(max_size):
    """The worker group in moto (M2b's shape), with the given maximum."""
    import boto3

    ec2 = boto3.client("ec2", region_name="us-east-1")
    ec2.create_launch_template(
        LaunchTemplateName="neurolens-worker-test",
        LaunchTemplateData={"ImageId": "ami-12c6146b", "InstanceType": "t3.micro"},
    )
    autoscaling = boto3.client("autoscaling", region_name="us-east-1")
    autoscaling.create_auto_scaling_group(
        AutoScalingGroupName=WORKER_GROUP,
        LaunchTemplate={"LaunchTemplateName": "neurolens-worker-test", "Version": "$Latest"},
        MinSize=0,
        MaxSize=max_size,
        DesiredCapacity=0,
        AvailabilityZones=["us-east-1a"],
    )
    return autoscaling


@pytest.fixture
def aws_calls(monkeypatch):
    """Every AWS operation any boto3 client makes, by name; `fail` makes the named ones raise."""
    from botocore.client import BaseClient
    from botocore.exceptions import ClientError

    real = BaseClient._make_api_call
    record = {"names": [], "fail": set()}

    def make_api_call(self, operation_name, api_params):
        record["names"].append(operation_name)
        if operation_name in record["fail"]:
            raise ClientError(
                {"Error": {"Code": "Throttling", "Message": "Rate exceeded"}}, operation_name
            )
        return real(self, operation_name, api_params)

    monkeypatch.setattr(BaseClient, "_make_api_call", make_api_call)
    return record


@pytest.fixture
def signed_in(cognito_client, idp):
    sign_in(cognito_client, idp, email="ann@example.com")
    return cognito_client


def test_presign_with_the_worker_group_at_max_0_is_503_and_reserves_nothing(signed_in, pg):
    worker_group(max_size=0)
    before = pg.snapshot()
    resp = signed_in.post("/api/uploads/presign", json=PRESIGN)
    assert resp.status_code == 503
    assert resp.get_json()["error"] == "processing_paused"
    assert "fields" not in resp.get_json()
    assert pg.snapshot() == before


def test_presign_with_the_worker_group_at_max_1_proceeds(signed_in, pg):
    worker_group(max_size=1)
    resp = signed_in.post("/api/uploads/presign", json=PRESIGN)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["reserved_cents"] == 90
    assert pg.job(data["job_id"])["status"] == "queued"


def test_presign_after_the_group_is_restarted_proceeds(signed_in):
    autoscaling = worker_group(max_size=0)
    assert signed_in.post("/api/uploads/presign", json=PRESIGN).status_code == 503
    autoscaling.update_auto_scaling_group(AutoScalingGroupName=WORKER_GROUP, MaxSize=1)
    assert signed_in.post("/api/uploads/presign", json=PRESIGN).status_code == 200


def test_presign_fails_closed_when_the_group_cannot_be_read(signed_in, pg, aws_calls):
    worker_group(max_size=1)
    aws_calls["fail"].add("DescribeAutoScalingGroups")
    before = pg.snapshot()
    resp = signed_in.post("/api/uploads/presign", json=PRESIGN)
    assert resp.status_code >= 500
    assert resp.get_json()["error"] == "presign_failed"
    assert "fields" not in resp.get_json()
    assert pg.snapshot() == before
    assert "DescribeAutoScalingGroups" in aws_calls["names"]


def test_presign_in_dev_mode_never_reads_a_worker_group(client, pg, aws_calls):
    aws_calls["fail"].add("DescribeAutoScalingGroups")
    resp = client.post("/api/uploads/presign", json=PRESIGN)
    assert resp.status_code == 200
    assert "DescribeAutoScalingGroups" not in aws_calls["names"]


# ---------------------------------------------------------------- database waking


def test_me_is_503_database_waking_within_the_45_s_budget(
    aws, make_cfg, tmp_path, waking_aurora, patch_everywhere
):
    """The web app builds its Data API database with resume_wait_s=45 (§2c), so a request ends
    before the Lambda's and CloudFront's 60 s timeouts."""
    client, clock = waking_aurora
    patch_everywhere(
        "data_api_client", lambda *a, **kw: client, "neurolens.db", "neurolens.web.app"
    )
    cfg = make_cfg(db={"backend": "data_api"})
    cfg["aws"].update(
        db_cluster_arn="arn:aws:rds:us-east-1:000000000000:cluster:neurolens-db",
        db_secret_arn="arn:aws:secretsmanager:us-east-1:000000000000:secret:x",
        db_name="neurolens",
    )
    app = create_app(cfg=cfg, data_dir=tmp_path / "data", s3_client=aws.s3)
    resp = app.test_client().get("/api/me")
    assert resp.status_code == 503
    assert resp.get_json()["error"] == "database_waking"
    assert 30 <= clock.elapsed <= 45
