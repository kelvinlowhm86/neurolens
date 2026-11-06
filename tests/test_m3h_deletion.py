"""M3h §6, §13: deleting either video removes its comparisons.

The comparison row and object go in the same purge as the job's own files, the partner video is
untouched, a failed S3 delete leaves the rows for the reaper, and the account can then be
removed (the foreign keys deliberately do not cascade).
"""

import json
import uuid

import pytest
from botocore.exceptions import ClientError
from conftest import STARTER_CENTS, WEB_USER_ID
from neurolens import billing, storage
from neurolens.web.app import create_app
from test_m3c_delete_job import DeleteReportsError
from test_m3g_report import job_in_state


def canonical(x, y):
    return tuple(sorted(str(uuid.UUID(str(value))) for value in (x, y)))


def comparison_key(a, b):
    return f"results/{a}_{b}.report.json"


def app_for(aws, db, make_cfg, tmp_path):
    app = create_app(
        cfg=make_cfg(),
        data_dir=tmp_path / "data",
        db=db,
        s3_client=aws.s3,
    )
    app.config["TESTING"] = True
    return app.test_client()


def done_pair(db, *, user_id=WEB_USER_ID):
    first, _ = job_in_state(db, "done", user_id=user_id)
    second, _ = job_in_state(db, "done", user_id=user_id)
    return canonical(first, second)


def seed_job(aws, job_id, *, user_id=WEB_USER_ID):
    object_key = f"uploads/{user_id}/{job_id}.mp4"
    keys = storage.job_keys(job_id, object_key)
    for key in keys:
        aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"file")
    return object_key, keys


def seed_comparison(aws, pg, a, b):
    aws.s3.put_object(
        Bucket=aws.bucket,
        Key=comparison_key(a, b),
        Body=json.dumps({"headline": "A beats B", "differences": []}).encode(),
    )
    pg.conn.execute(
        "INSERT INTO comparisons (job_a, job_b, state, tries, started_at) "
        "VALUES (%s::uuid, %s::uuid, 'ready', 1, now())",
        (a, b),
    )


def object_exists(aws, key):
    try:
        aws.s3.head_object(Bucket=aws.bucket, Key=key)
    except ClientError as err:
        assert err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound")
        return False
    return True


@pytest.mark.parametrize("deleted", [0, 1])
def test_deleting_either_video_removes_the_comparison_and_leaves_the_partner(
    aws, db, pg, make_cfg, tmp_path, deleted
):
    client = app_for(aws, db, make_cfg, tmp_path)
    a, b = done_pair(db)
    _, a_keys = seed_job(aws, a)
    _, b_keys = seed_job(aws, b)
    seed_comparison(aws, pg, a, b)
    victim, partner = (a, b) if deleted == 0 else (b, a)
    partner_keys = a_keys if partner == a else b_keys

    resp = client.delete(f"/api/jobs/{victim}")

    assert resp.status_code == 200
    assert pg.rows("SELECT * FROM comparisons") == []
    assert not object_exists(aws, comparison_key(a, b))
    assert pg.job(victim)["deleted_at"] is not None
    assert pg.job(partner)["deleted_at"] is None
    assert all(object_exists(aws, key) for key in partner_keys)


def test_account_delete_removes_every_comparison_and_then_the_account(
    aws, db, pg, make_cfg, tmp_path
):
    user = "account-user"
    billing.ensure_user(db, user, "account@example.com", STARTER_CENTS)
    a, b = done_pair(db, user_id=user)
    seed_job(aws, a, user_id=user)
    seed_job(aws, b, user_id=user)
    seed_comparison(aws, pg, a, b)

    billing.delete_account(db, user)
    for job_id in (a, b):
        billing.purge_job_files(db, aws.s3, aws.bucket, job_id)

    assert pg.rows("SELECT * FROM comparisons") == []
    assert not object_exists(aws, comparison_key(a, b))
    assert billing.remove_deleted_account(db, user) is True
    assert pg.one("SELECT * FROM users WHERE user_id = %s", user) is None
    assert pg.rows("SELECT * FROM jobs WHERE user_id = %s", user) == []


def test_a_failed_s3_delete_keeps_the_comparison_row_and_object_for_the_reaper(
    aws, db, pg, make_cfg, tmp_path
):
    a, b = done_pair(db)
    seed_job(aws, a)
    seed_job(aws, b)
    seed_comparison(aws, pg, a, b)
    assert billing.delete_job(db, WEB_USER_ID, a) is True

    with pytest.raises(RuntimeError):
        billing.purge_job_files(db, DeleteReportsError(aws.s3), aws.bucket, a)

    assert len(pg.rows("SELECT * FROM comparisons")) == 1
    assert object_exists(aws, comparison_key(a, b))
    assert pg.job(a)["dataset_purged_at"] is None

    billing.purge_job_files(db, aws.s3, aws.bucket, a)

    assert pg.rows("SELECT * FROM comparisons") == []
    assert not object_exists(aws, comparison_key(a, b))
    assert pg.job(a)["dataset_purged_at"] is not None
