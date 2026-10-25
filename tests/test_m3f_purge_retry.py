"""M3f purge retry for job deletion (docs/M3f_spec.md §5c).

A job becomes invisible the moment it is marked deleted, so the route answers success even if
the purge fails; the reaper then retries the purge for every marked job, consented or not. For a
deleted job that purge also removes the user's own files (upload, result, brain, thumbnail). A
copy that was only removed (Stop sharing, the 90-day rule) loses its two prefixes and keeps the
user's own result.
"""

import json

import pytest

from test_m3f_sharing import (
    DeleteReportsErrors,
    app_for,
    make_job,
    object_exists,
    run_reaper,
    seed_research,
)

DAY = 24 * 60 * 60


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


@pytest.mark.parametrize("consent", [True, False], ids=["consented", "not_consented"])
def test_a_failed_job_delete_purge_answers_success_and_the_reaper_finishes_it(
    aws, db, pg, make_cfg, tmp_path, reaper_env, consent
):
    reaper_env(90)
    client = app_for(aws, db, make_cfg, tmp_path, s3_client=DeleteReportsErrors(aws.s3))
    job_id, key = make_job(db, consent=consent)
    dataset, academic, own = seed_research(aws, job_id, key)

    resp = client.delete(f"/api/jobs/{job_id}")

    assert resp.status_code == 200
    assert resp.get_json() == {"deleted": True}
    job = pg.job(job_id)
    assert job["deleted_at"] is not None
    assert job["dataset_purged_at"] is None
    assert all(object_exists(aws, name) for name in [*dataset, *academic, *own])

    run_reaper()

    job = pg.job(job_id)
    assert job["dataset_purged_at"] is not None
    assert not any(object_exists(aws, name) for name in [*dataset, *academic, *own])


def test_the_reaper_leaves_a_job_that_was_neither_deleted_nor_removed(
    aws, db, pg, make_cfg, tmp_path, reaper_env
):
    reaper_env(90)
    job_id, key = make_job(db)
    dataset, academic, own = seed_research(aws, job_id, key)

    run_reaper()

    job = pg.job(job_id)
    assert job["dataset_removed_at"] is None
    assert job["dataset_purged_at"] is None
    assert all(object_exists(aws, name) for name in [*dataset, *academic, *own])


@pytest.mark.parametrize("path", ["stop_sharing", "no_curve"])
def test_a_removed_copy_loses_only_its_prefixes_and_keeps_the_users_own_files(
    aws, db, pg, make_cfg, tmp_path, reaper_env, path
):
    reaper_env(90)
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, key = make_job(db)
    dataset, academic, own = seed_research(aws, job_id, key)

    if path == "stop_sharing":
        assert client.post(f"/api/jobs/{job_id}/stop-sharing").status_code == 200
    else:
        pg.age(job_id, created_s=91 * DAY)
        run_reaper()

    job = pg.job(job_id)
    assert job["dataset_removed_at"] is not None
    assert job["dataset_purged_at"] is not None
    assert not any(object_exists(aws, name) for name in [*dataset, *academic])
    assert all(object_exists(aws, name) for name in own)
