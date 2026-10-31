"""M3e reaper (docs/M3e_spec.md §5 "Reaper", §7 "Clean-up", §11 "Reaper").

The reaper is the backstop for a lost GPU notification: any open job four hours after creation
is refunded, every failed job's files are retried for two days, and a consented dataset copy
without a retention snapshot is deleted after the configured limit. Money and files are read
directly.
"""

import json

import pytest
from botocore.exceptions import ClientError
from conftest import STARTER_CENTS, USER_ID
from neurolens import billing, pricing, storage

DAY = 24 * 60 * 60
DEADLINE_S = 4 * 60 * 60
RETENTION_DAYS = 90


def reaper_module():
    from neurolens.lambdas import reaper

    return reaper


def run_reaper():
    reaper_module().handler({}, None)


@pytest.fixture
def lambda_env(aws, db, monkeypatch, tmp_path, patch_everywhere):
    """The handler builds its database through the same factory AWS gives it; S3 is moto."""
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
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    (root / "config.json").write_text(json.dumps({"research": {"no_curve_days": 90}}))
    monkeypatch.setenv("NEUROLENS_ROOT", str(root))

    def data_api_database(*args, **kwargs):
        return db

    patch_everywhere(
        "DataApiDatabase", data_api_database, "neurolens.db", "neurolens.lambdas.reaper"
    )


class DeleteFails:
    """A boto3 S3 client whose deletes for one key raise, as S3 may."""

    def __init__(self, inner, key):
        self._inner = inner
        self.key = key

    def delete_object(self, **kwargs):
        if kwargs.get("Key") == self.key:
            raise ClientError(
                {"Error": {"Code": "InternalError", "Message": "delete failed"}}, "DeleteObject"
            )
        return self._inner.delete_object(**kwargs)

    def delete_objects(self, **kwargs):
        keys = [obj["Key"] for obj in kwargs.get("Delete", {}).get("Objects", [])]
        if self.key in keys:
            raise ClientError(
                {"Error": {"Code": "InternalError", "Message": "delete failed"}}, "DeleteObjects"
            )
        return self._inner.delete_objects(**kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def upload(aws, key):
    aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"an uploaded video")


def object_exists(aws, key):
    return storage.object_exists(aws.s3, aws.bucket, key)


def consented_done_job(db, new_key, *, consent=True):
    job_id, key = new_key()
    billing.ensure_user(db, USER_ID, f"{USER_ID}@example.com", STARTER_CENTS)
    billing.reserve(db, USER_ID, job_id, key, "clip.mp4", 3000, research_consent=consent)
    assert billing.settle_success(db, job_id) is True
    return job_id, key


def write_job_files(aws, job_id, key):
    """The upload, the research copy, and the user's own result, brain and thumbnail."""
    upload(aws, key)
    storage.put_dataset(aws.s3, aws.bucket, job_id, key, {"job_id": job_id}, brain=b"dataset brain")
    assert storage.put_result(aws.s3, aws.bucket, job_id, {"job_id": job_id}) is True
    aws.s3.put_object(Bucket=aws.bucket, Key=f"results/{job_id}.brain", Body=b"brain")
    aws.s3.put_object(Bucket=aws.bucket, Key=f"results/{job_id}.jpg", Body=b"thumbnail")


def dataset_keys(job_id):
    return [
        f"dataset/{job_id}/video.mp4",
        f"academic/{job_id}/result.json",
        f"academic/{job_id}/brain",
    ]


def user_keys(job_id, key):
    return [
        key,
        f"results/{job_id}.json",
        f"results/{job_id}.brain",
        f"results/{job_id}.jpg",
    ]


def add_snapshot(pg, job_id):
    import uuid

    pg.conn.execute(
        "INSERT INTO retention_snapshots (snapshot_id, job_id, curve_sha256, points) "
        "VALUES (%s::uuid, %s::uuid, %s, %s)",
        (str(uuid.uuid4()), job_id, str(uuid.uuid4()), 20),
    )


def dataset_files_exist(aws, job_id):
    return [key for key in dataset_keys(job_id) if object_exists(aws, key)]


# ---------------------------------------------------------------- the four-hour deadline


def test_a_queued_job_past_four_hours_is_refunded_deadline_passed(aws, db, pg, lambda_env, new_job):
    assert reaper_module().DEADLINE_S == DEADLINE_S
    job_id, key = new_job()
    upload(aws, key)
    pg.age(job_id, updated_s=DEADLINE_S + 3600, created_s=DEADLINE_S + 3600)

    run_reaper()

    job = pg.job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "deadline_passed"
    assert pg.balance() == (STARTER_CENTS, 0)
    assert not object_exists(aws, key)


def test_a_queued_job_under_four_hours_is_left_alone(aws, db, pg, lambda_env, new_job):
    job_id, key = new_job()
    upload(aws, key)
    pg.age(job_id, updated_s=DEADLINE_S - 3600, created_s=DEADLINE_S - 3600)
    before = pg.snapshot()

    run_reaper()

    assert pg.snapshot() == before
    assert object_exists(aws, key)


# ---------------------------------------------------------------- delete retries


def test_a_failed_jobs_leftover_files_are_deleted_without_a_second_refund(
    aws, db, pg, lambda_env, new_job
):
    job_id, key = new_job()
    upload(aws, key)
    assert billing.issue_refund(db, job_id, "processing_failed", queued=True) is True
    ledger = pg.ledger()

    run_reaper()

    assert pg.job(job_id)["status"] == "failed"
    assert pg.ledger() == ledger
    assert pg.balance() == (STARTER_CENTS, 0)
    assert not object_exists(aws, key)


def test_a_failed_job_older_than_two_days_is_not_touched(aws, db, pg, lambda_env, new_job):
    job_id, key = new_job()
    upload(aws, key)
    assert billing.issue_refund(db, job_id, "processing_failed", queued=True) is True
    pg.age(job_id, updated_s=3 * DAY)
    before = pg.snapshot()

    run_reaper()

    assert pg.snapshot() == before
    assert object_exists(aws, key)


def test_a_done_jobs_files_are_never_deleted(aws, db, pg, lambda_env, new_job):
    job_id, key = new_job()
    upload(aws, key)
    assert storage.put_result(aws.s3, aws.bucket, job_id, {"job_id": job_id}) is True
    assert billing.settle_success(db, job_id) is True
    pg.age(job_id, updated_s=3600, created_s=3600)

    run_reaper()

    job = pg.job(job_id)
    assert job["status"] == "done" and job["captured_cents"] == pricing.estimate_cost_cents(3)
    assert object_exists(aws, key)
    assert object_exists(aws, f"results/{job_id}.json")


def test_a_delete_that_still_fails_makes_the_run_raise(
    aws, db, pg, lambda_env, new_job, monkeypatch
):
    job_id, key = new_job()
    upload(aws, key)
    assert billing.issue_refund(db, job_id, "processing_failed", queued=True) is True
    ledger = pg.ledger()

    import boto3

    real_client = boto3.client

    def client(service_name, *args, **kwargs):
        inner = real_client(service_name, *args, **kwargs)
        return DeleteFails(inner, key) if service_name == "s3" else inner

    monkeypatch.setattr(boto3, "client", client)
    with pytest.raises(Exception):  # noqa: B017 - the spec says the run raises
        run_reaper()

    assert pg.job(job_id)["status"] == "failed"
    assert pg.ledger() == ledger
    assert object_exists(aws, key)


# ---------------------------------------------------------------- the sixty-day clean-up


def test_clean_up_deletes_the_research_copy_and_marks_the_dataset_removed(
    aws, db, pg, lambda_env, new_key
):
    job_id, key = consented_done_job(db, new_key)
    write_job_files(aws, job_id, key)
    pg.age(job_id, created_s=(RETENTION_DAYS + 1) * DAY)

    run_reaper()

    job = pg.job(job_id)
    assert job["dataset_removed_at"] is not None
    assert job["retention_added_at"] is None
    assert dataset_files_exist(aws, job_id) == []
    for kept in user_keys(job_id, key):
        assert object_exists(aws, kept)


def test_a_retention_snapshot_skips_the_delete(aws, db, pg, lambda_env, new_key):
    job_id, key = consented_done_job(db, new_key)
    write_job_files(aws, job_id, key)
    add_snapshot(pg, job_id)
    pg.age(job_id, created_s=(RETENTION_DAYS + 1) * DAY)

    run_reaper()

    job = pg.job(job_id)
    assert job["dataset_removed_at"] is None
    assert set(dataset_files_exist(aws, job_id)) == set(dataset_keys(job_id))
    for kept in user_keys(job_id, key):
        assert object_exists(aws, kept)


def test_a_job_under_sixty_days_is_left_alone(aws, db, pg, lambda_env, new_key):
    job_id, key = consented_done_job(db, new_key)
    write_job_files(aws, job_id, key)
    pg.age(job_id, created_s=(RETENTION_DAYS - 1) * DAY)

    run_reaper()

    job = pg.job(job_id)
    assert job["dataset_removed_at"] is None
    assert job["retention_added_at"] is None
    assert set(dataset_files_exist(aws, job_id)) == set(dataset_keys(job_id))


def test_a_job_without_consent_is_left_alone(aws, db, pg, lambda_env, new_key):
    job_id, key = consented_done_job(db, new_key, consent=False)
    write_job_files(aws, job_id, key)
    pg.age(job_id, created_s=(RETENTION_DAYS + 1) * DAY)

    run_reaper()

    job = pg.job(job_id)
    assert job["dataset_removed_at"] is None
    assert set(dataset_files_exist(aws, job_id)) == set(dataset_keys(job_id))


def test_a_job_that_already_has_a_snapshot_is_left_alone(aws, db, pg, lambda_env, new_key):
    job_id, key = consented_done_job(db, new_key)
    write_job_files(aws, job_id, key)
    add_snapshot(pg, job_id)
    pg.age(job_id, created_s=(RETENTION_DAYS + 1) * DAY)

    run_reaper()

    job = pg.job(job_id)
    assert job["dataset_removed_at"] is None
    assert set(dataset_files_exist(aws, job_id)) == set(dataset_keys(job_id))


def test_a_dataset_already_removed_without_a_purge_is_finished(aws, db, pg, lambda_env, new_key):
    job_id, key = consented_done_job(db, new_key)
    write_job_files(aws, job_id, key)
    pg.conn.execute("UPDATE jobs SET dataset_removed_at = now() WHERE job_id = %s::uuid", (job_id,))
    pg.age(job_id, created_s=(RETENTION_DAYS + 1) * DAY)

    run_reaper()

    job = pg.job(job_id)
    assert job["dataset_removed_at"] is not None
    assert job["dataset_purged_at"] is not None
    assert dataset_files_exist(aws, job_id) == []
    for kept in user_keys(job_id, key):
        assert object_exists(aws, kept)


def test_a_failed_dataset_delete_marks_the_row_and_raises(
    aws, db, pg, lambda_env, new_key, monkeypatch
):
    job_id, key = consented_done_job(db, new_key)
    write_job_files(aws, job_id, key)
    pg.age(job_id, created_s=(RETENTION_DAYS + 1) * DAY)
    video_key = f"dataset/{job_id}/video.mp4"

    import boto3

    real_client = boto3.client

    def client(service_name, *args, **kwargs):
        inner = real_client(service_name, *args, **kwargs)
        return DeleteFails(inner, video_key) if service_name == "s3" else inner

    monkeypatch.setattr(boto3, "client", client)
    with pytest.raises(Exception):  # noqa: B017 - the spec says the run raises
        run_reaper()

    job = pg.job(job_id)
    assert job["dataset_removed_at"] is not None
    assert job["dataset_removed_reason"] == "no_curve"
    assert job["dataset_purged_at"] is None
    assert job["retention_added_at"] is None
    assert set(dataset_files_exist(aws, job_id)) == set(dataset_keys(job_id))
