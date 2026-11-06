"""M3l's Real viewers strip route (docs/M3l_spec.md §4, §7).

`GET /api/jobs/<id>/retention` gives the caller the newest quality-checked curve stored for their
own job as points and the video's duration. Another person's job and a job without a curve are
both 404. PostgreSQL and moto S3 are read directly.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import STARTER_CENTS, WEB_USER_ID
from neurolens import billing, results, storage

OTHER_USER = "other-user"


def make_done_job(db, *, user_id=WEB_USER_ID, seconds=30.0, sha="a" * 64):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", STARTER_CENTS)
    job_id = str(uuid.uuid4())
    key = storage.object_key(user_id, job_id, "video/mp4")
    billing.reserve(
        db, user_id, job_id, key, "clip.mp4", round(seconds * 1000), research_consent=True
    )
    assert billing.verify_queued(db, job_id, round(seconds * 1000), 3600) == "ok"
    assert billing.claim(db, job_id) == 1
    assert billing.settle_success(db, job_id) is True
    billing.record_video_hash(db, job_id, sha)
    return job_id


def curve(*, last=0.5, points=20):
    return [(i / (points - 1), 1.0 + (last - 1.0) * i / (points - 1)) for i in range(points)]


def add_snapshot(
    aws, db, pg, job_id, *, sha, pairs=None, flags=(), video_seconds=30, added_at=None
):
    snapshot_id = str(uuid.uuid4())
    csv_key = f"dataset/{job_id}/retention/{snapshot_id}.csv"
    pairs = curve() if pairs is None else pairs
    aws.s3.put_object(Bucket=aws.bucket, Key=csv_key, Body=results.retention_csv(pairs).encode())
    pg.conn.execute(
        "INSERT INTO retention_snapshots "
        "(snapshot_id, job_id, added_at, curve_sha256, csv_key, quality_flags, video_seconds) "
        "VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s)",
        (
            snapshot_id,
            job_id,
            added_at or datetime.now(UTC),
            sha,
            csv_key,
            list(flags),
            video_seconds,
        ),
    )
    return snapshot_id


@pytest.fixture
def client(aws, db, make_cfg, tmp_path):
    from neurolens.web.app import create_app

    app = create_app(
        cfg=make_cfg(),
        data_dir=tmp_path / "data",
        db=db,
        s3_client=aws.s3,
    )
    app.config["TESTING"] = True
    return app.test_client()


def test_the_owner_gets_the_stored_curve_as_seconds_and_percent(aws, db, pg, client):
    job_id = make_done_job(db)
    add_snapshot(aws, db, pg, job_id, sha="a" * 64)

    resp = client.get(f"/api/jobs/{job_id}/retention")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["duration_s"] == pytest.approx(30)
    assert len(data["points"]) == 20
    first, last = data["points"][0], data["points"][-1]
    assert first[0] == pytest.approx(0)
    assert first[1] == pytest.approx(100)
    assert last[0] == pytest.approx(30)
    assert last[1] == pytest.approx(50)


def test_the_newest_quality_checked_curve_is_the_one_shown(aws, db, pg, client):
    job_id = make_done_job(db)
    add_snapshot(
        aws,
        db,
        pg,
        job_id,
        sha="a" * 64,
        pairs=curve(last=0.5),
        added_at=datetime.now(UTC) - timedelta(hours=1),
    )
    add_snapshot(
        aws,
        db,
        pg,
        job_id,
        sha="b" * 64,
        pairs=curve(last=1.0),
        flags=["flat_curve"],
    )

    resp = client.get(f"/api/jobs/{job_id}/retention")

    assert resp.status_code == 200
    assert resp.get_json()["points"][-1][1] == pytest.approx(50)


def test_another_persons_job_is_404(aws, db, pg, client):
    job_id = make_done_job(db, user_id=OTHER_USER, sha="b" * 64)
    add_snapshot(aws, db, pg, job_id, sha="c" * 64)

    resp = client.get(f"/api/jobs/{job_id}/retention")

    assert resp.status_code == 404


def test_a_job_without_a_curve_is_404(db, client):
    job_id = make_done_job(db)

    resp = client.get(f"/api/jobs/{job_id}/retention")

    assert resp.status_code == 404
