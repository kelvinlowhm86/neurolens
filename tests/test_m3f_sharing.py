"""M3f research sharing (docs/M3f_spec.md §5a-§5c, §10 "delete_job_prefixes", "Stop sharing",
"Reaper", "Consent").

`storage.delete_job_prefixes` pages through `dataset/<job_id>/` and `academic/<job_id>/`, refuses
anything that is not a UUID and raises when S3 reports a failed delete. The stop-sharing route
deletes before it marks the copy removed. The reaper's 90-day clean-up counts from
`research.no_curve_days` and keeps a job that has any snapshot. Job and account deletion use the
same two prefixes. Consent stores the version and time only when it is given.
"""

import json
import uuid
from pathlib import PurePosixPath

import pytest
from botocore.client import BaseClient
from conftest import STARTER_CENTS, WEB_USER_ID, sign_in
from neurolens import billing, storage
from neurolens.web.app import create_app

OTHER_USER = "someone-else"
DAY = 24 * 60 * 60


def app_for(aws, db, make_cfg, tmp_path, s3_client=None):
    cfg = make_cfg(
        research={
            "curve_rewards_per_window": 5,
            "curve_reward_window_days": 30,
            "no_curve_days": 90,
        }
    )
    app = create_app(
        cfg=cfg,
        data_dir=tmp_path / "data",
        db=db,
        s3_client=s3_client or aws.s3,
    )
    app.config["TESTING"] = True
    return app.test_client()


def make_job(
    db,
    *,
    user_id=WEB_USER_ID,
    state="done",
    consent=True,
    content_type="video/mp4",
):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", STARTER_CENTS)
    job_id = str(uuid.uuid4())
    key = storage.object_key(user_id, job_id, content_type)
    billing.reserve(db, user_id, job_id, key, f"clip{PurePosixPath(key).suffix}", 3000,
                    research_consent=consent)
    if state in ("processing", "done"):
        assert billing.claim(db, job_id) == 1
    if state == "done":
        assert billing.settle_success(db, job_id) is True
    return job_id, key


def seed_research(aws, job_id, key):
    """The research copy plus the user's own result files."""
    ext = PurePosixPath(key).suffix
    dataset = [
        f"dataset/{job_id}/video{ext}",
        f"dataset/{job_id}/retention/{uuid.uuid4()}.json",
        f"dataset/{job_id}/retention/{uuid.uuid4()}.csv",
    ]
    academic = [
        f"academic/{job_id}/result.json",
        f"academic/{job_id}/brain",
    ]
    own = [
        key,
        f"results/{job_id}.json",
        f"results/{job_id}.brain",
        f"results/{job_id}.jpg",
    ]
    for name in [*dataset, *academic, *own]:
        aws.s3.put_object(Bucket=aws.bucket, Key=name, Body=b"x")
    return dataset, academic, own


def object_exists(aws, key):
    return storage.object_exists(aws.s3, aws.bucket, key)


def prefix_keys(aws, prefix):
    pages = aws.s3.get_paginator("list_objects_v2").paginate(
        Bucket=aws.bucket, Prefix=prefix
    )
    return [obj["Key"] for page in pages for obj in page.get("Contents", [])]


class NoCallS3:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"s3.{name} must not be called here")

        return call


class DeleteReportsErrors:
    """A real S3 client whose deletes fail for every key, as S3 may."""

    def __init__(self, inner):
        self._inner = inner

    def delete_objects(self, **kwargs):
        objects = kwargs.get("Delete", {}).get("Objects", [])
        return {
            "Errors": [{"Key": obj["Key"], "Code": "InternalError"} for obj in objects],
            "Deleted": [],
        }

    def delete_object(self, **kwargs):
        raise RuntimeError("delete failed")

    def __getattr__(self, name):
        return getattr(self._inner, name)


# ---------------------------------------------------------------- delete_job_prefixes


def test_delete_job_prefixes_deletes_every_object_under_both_prefixes_across_pages(aws):
    job_id = str(uuid.uuid4())
    other = str(uuid.uuid4())
    for prefix in (f"dataset/{job_id}/objects", f"academic/{job_id}/objects"):
        for number in range(1001):
            aws.s3.put_object(
                Bucket=aws.bucket, Key=f"{prefix}/{number:04d}", Body=b"x"
            )
    aws.s3.put_object(Bucket=aws.bucket, Key=f"dataset/{other}/video.mp4", Body=b"x")

    storage.delete_job_prefixes(aws.s3, aws.bucket, job_id)

    assert prefix_keys(aws, f"dataset/{job_id}/") == []
    assert prefix_keys(aws, f"academic/{job_id}/") == []
    assert object_exists(aws, f"dataset/{other}/video.mp4")


@pytest.mark.parametrize(
    "job_id",
    ["", "../", "not-a-uuid", f"{uuid.uuid4()}/../other", f"{uuid.uuid4()}/x"],
    ids=["empty", "dots", "text", "traversal", "suffix"],
)
def test_delete_job_prefixes_refuses_a_non_uuid_before_listing(aws, job_id):
    decoy = f"dataset/{uuid.uuid4()}/video.mp4"
    aws.s3.put_object(Bucket=aws.bucket, Key=decoy, Body=b"x")
    s3 = NoCallS3()

    with pytest.raises(ValueError):
        storage.delete_job_prefixes(s3, aws.bucket, job_id)

    assert s3.calls == []
    assert object_exists(aws, decoy)


def test_delete_job_prefixes_raises_when_s3_reports_a_failed_delete(aws):
    job_id = str(uuid.uuid4())
    key = f"dataset/{job_id}/video.mp4"
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"x")

    delete = getattr(storage, "delete_job_prefixes", None)
    assert callable(delete), "storage.delete_job_prefixes is missing"
    with pytest.raises(Exception):  # noqa: B017 - the spec names no exception type here
        delete(DeleteReportsErrors(aws.s3), aws.bucket, job_id)

    assert object_exists(aws, key)


# ---------------------------------------------------------------- stop sharing


def test_stop_sharing_deletes_both_prefixes_then_marks_the_copy_removed(
    aws, db, pg, make_cfg, tmp_path
):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, key = make_job(db)
    dataset, academic, own = seed_research(aws, job_id, key)

    resp = client.post(f"/api/jobs/{job_id}/stop-sharing")

    assert 200 <= resp.status_code < 300
    assert not any(object_exists(aws, name) for name in [*dataset, *academic])
    assert all(object_exists(aws, name) for name in own)
    job = pg.job(job_id)
    assert job["dataset_removed_at"] is not None
    assert job["dataset_removed_reason"] == "stopped"


def test_a_failed_delete_leaves_the_stop_sharing_row_unmarked(aws, db, pg, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path, s3_client=DeleteReportsErrors(aws.s3))
    job_id, key = make_job(db)
    dataset, academic, _ = seed_research(aws, job_id, key)

    resp = client.post(f"/api/jobs/{job_id}/stop-sharing")

    assert resp.status_code == 500
    assert all(object_exists(aws, name) for name in [*dataset, *academic])
    job = pg.job(job_id)
    assert job["dataset_removed_at"] is None
    assert job["dataset_removed_reason"] is None


@pytest.mark.parametrize("processing", [False, True], ids=["queued", "processing"])
def test_stop_sharing_an_active_job_is_409_and_deletes_nothing(
    aws, db, pg, make_cfg, tmp_path, processing
):
    job_id, _ = make_job(db, state="processing" if processing else "queued")
    s3 = NoCallS3()

    resp = app_for(aws, db, make_cfg, tmp_path, s3_client=s3).post(
        f"/api/jobs/{job_id}/stop-sharing"
    )

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "job_active"
    assert s3.calls == []
    assert pg.job(job_id)["dataset_removed_at"] is None


@pytest.mark.parametrize("case", ["not_shared", "already_removed"])
def test_stop_sharing_a_job_that_is_not_shared_is_409_not_shared(
    aws, db, pg, make_cfg, tmp_path, case
):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db, consent=(case == "already_removed"))
    if case == "already_removed":
        pg.conn.execute(
            "UPDATE jobs SET dataset_removed_at = now(), dataset_removed_reason = 'no_curve' "
            "WHERE job_id = %s::uuid",
            (job_id,),
        )

    resp = client.post(f"/api/jobs/{job_id}/stop-sharing")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "not_shared"


def test_another_users_job_is_404(aws, db, pg, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db, user_id=OTHER_USER)

    resp = client.post(f"/api/jobs/{job_id}/stop-sharing")

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"


# ---------------------------------------------------------------- the reaper's 90 days


@pytest.fixture
def reaper_env(aws, db, monkeypatch, tmp_path, patch_everywhere):
    """The reaper handler on the test database, with config.json's research block configurable."""
    monkeypatch.setenv(
        "NEUROLENS_DB_CLUSTER_ARN", "arn:aws:rds:us-east-1:000000000000:cluster:neurolens-db"
    )
    monkeypatch.setenv(
        "NEUROLENS_DB_SECRET_ARN",
        "arn:aws:secretsmanager:us-east-1:000000000000:secret:rds!cluster-test",
    )
    monkeypatch.setenv("NEUROLENS_DB_NAME", "neurolens")
    monkeypatch.setenv("NEUROLENS_S3_BUCKET", aws.bucket)
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    def data_api_database(*args, **kwargs):
        return db

    patch_everywhere(
        "DataApiDatabase", data_api_database, "neurolens.db", "neurolens.lambdas.reaper"
    )

    def install(no_curve_days):
        root = tmp_path / f"root-{no_curve_days}"
        (root / "data").mkdir(parents=True)
        config = {"research": {"no_curve_days": no_curve_days}}
        (root / "config.json").write_text(json.dumps(config))
        monkeypatch.setenv("NEUROLENS_ROOT", str(root))

    return install


def run_reaper():
    from neurolens.lambdas import reaper

    reaper.handler({}, None)


def insert_snapshot(pg, job_id):
    pg.conn.execute(
        "INSERT INTO retention_snapshots (snapshot_id, job_id, curve_sha256, points) "
        "VALUES (%s::uuid, %s::uuid, %s, %s)",
        (str(uuid.uuid4()), job_id, str(uuid.uuid4()), 20),
    )


def test_the_reaper_deletes_the_copy_after_90_days_and_says_no_curve(
    aws, db, pg, reaper_env, make_cfg, tmp_path
):
    reaper_env(90)
    job_id, key = make_job(db)
    dataset, academic, own = seed_research(aws, job_id, key)
    pg.age(job_id, created_s=91 * DAY)

    run_reaper()

    job = pg.job(job_id)
    assert job["dataset_removed_at"] is not None
    assert job["dataset_removed_reason"] == "no_curve"
    assert not any(object_exists(aws, name) for name in [*dataset, *academic])
    assert all(object_exists(aws, name) for name in own)


def test_the_reaper_keeps_a_copy_younger_than_its_configured_limit(
    aws, db, pg, reaper_env
):
    reaper_env(90)
    job_id, key = make_job(db)
    dataset, academic, _ = seed_research(aws, job_id, key)
    pg.age(job_id, created_s=89 * DAY)

    run_reaper()

    job = pg.job(job_id)
    assert job["dataset_removed_at"] is None
    assert all(object_exists(aws, name) for name in [*dataset, *academic])


def test_the_reaper_reads_the_limit_from_config(aws, db, pg, reaper_env):
    reaper_env(2)
    job_id, key = make_job(db)
    seed_research(aws, job_id, key)
    pg.age(job_id, created_s=3 * DAY)

    run_reaper()

    assert pg.job(job_id)["dataset_removed_reason"] == "no_curve"


def test_the_reaper_keeps_a_job_that_has_any_snapshot(aws, db, pg, reaper_env):
    reaper_env(90)
    job_id, key = make_job(db)
    dataset, academic, _ = seed_research(aws, job_id, key)
    insert_snapshot(pg, job_id)
    pg.age(job_id, created_s=91 * DAY)

    run_reaper()

    job = pg.job(job_id)
    assert job["dataset_removed_at"] is None
    assert all(object_exists(aws, name) for name in [*dataset, *academic])


# ---------------------------------------------------------------- consent


def test_consent_stores_the_version_and_time(db, pg):
    job_id, _ = make_job(db, consent=True)
    job = pg.job(job_id)
    assert billing.CONSENT_VERSION == "2026-10-25"
    assert job["consent_version"] == billing.CONSENT_VERSION
    assert job["consented_at"] is not None


def test_no_consent_leaves_the_version_and_time_empty(db, pg):
    job_id, _ = make_job(db, consent=False)
    job = pg.job(job_id)
    assert job["consent_version"] is None
    assert job["consented_at"] is None


# ---------------------------------------------------------------- job and account deletion


def test_job_delete_removes_both_prefixes(aws, db, pg, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, key = make_job(db)
    dataset, academic, own = seed_research(aws, job_id, key)

    resp = client.delete(f"/api/jobs/{job_id}")

    assert resp.status_code in (200, 204)
    assert not any(object_exists(aws, name) for name in [*dataset, *academic, *own])
    assert pg.job(job_id)["deleted_at"] is not None


@pytest.fixture
def cognito_admin(monkeypatch):
    """Enough of Cognito's admin API for account deletion: no users to delete."""

    real = BaseClient._make_api_call

    def make_api_call(self, operation_name, api_params):
        if operation_name == "ListUsers":
            return {"Users": []}
        if operation_name == "AdminDeleteUser":
            return {}
        return real(self, operation_name, api_params)

    monkeypatch.setattr(BaseClient, "_make_api_call", make_api_call)


def test_account_delete_removes_both_prefixes(
    aws, db, pg, cognito_client, idp, cognito_admin
):
    assert sign_in(cognito_client, idp, email="ann@example.com").status_code in (302, 303)
    user_id = pg.one("SELECT user_id FROM users WHERE lower(email) = 'ann@example.com'")["user_id"]
    job_id, key = make_job(db, user_id=user_id)
    dataset, academic, _ = seed_research(aws, job_id, key)

    resp = cognito_client.post("/api/account/delete", json={"confirm": "DELETE"})

    assert resp.status_code == 200
    assert not any(object_exists(aws, name) for name in [*dataset, *academic])
