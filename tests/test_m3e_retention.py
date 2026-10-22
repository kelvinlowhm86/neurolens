"""M3e retention (docs/M3e_spec.md §7, §11 "Retention").

A successful upload records `retention_added_at`; after the sixty-day clean-up removed the
research copy the route refuses with 409 `dataset_removed`. Job history exposes
`needs_retention` only for a done, consented job with neither marker.
"""

import json
import uuid

from conftest import WEB_USER_ID
from neurolens import billing, storage
from neurolens.web.app import create_app

STARTER_CENTS = 10_000
COLUMN_HEADERS = [
    {"name": "elapsedVideoTimeRatio", "columnType": "DIMENSION", "dataType": "FLOAT"},
    {"name": "audienceWatchRatio", "columnType": "METRIC", "dataType": "FLOAT"},
]


def report_text(rows):
    return json.dumps(
        {
            "kind": "youtubeAnalytics#resultTable",
            "columnHeaders": COLUMN_HEADERS,
            "rows": rows,
        }
    )


def valid_report():
    return report_text([[0.0, 0.0], [0.5, 1.0], [1.0, 0.8]])


def retention_key(job_id):
    return f"dataset/{job_id}/retention.csv"


def object_exists(aws, key):
    return storage.object_exists(aws.s3, aws.bucket, key)


def app_for(aws, db, make_cfg, tmp_path):
    app = create_app(
        cfg=make_cfg(),
        data_dir=tmp_path / "data",
        db=db,
        s3_client=aws.s3,
    )
    app.config["TESTING"] = True
    return app.test_client()


def make_job(db, *, consent=True, done=True, processing=False):
    billing.ensure_user(db, WEB_USER_ID, f"{WEB_USER_ID}@example.com", STARTER_CENTS)
    job_id = str(uuid.uuid4())
    key = storage.object_key(WEB_USER_ID, job_id, "video/mp4")
    billing.reserve(db, WEB_USER_ID, job_id, key, "clip.mp4", 3000, research_consent=consent)
    if processing:
        assert billing.claim(db, job_id) == 1
    if done:
        assert billing.settle_success(db, job_id) is True
    return job_id, key


# ---------------------------------------------------------------- the upload route


def test_a_successful_upload_records_the_retention_curve(aws, db, pg, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db, consent=True, done=True)

    resp = client.post(f"/api/jobs/{job_id}/retention", json={"report": valid_report()})

    assert 200 <= resp.status_code < 300
    job = pg.job(job_id)
    assert job["retention_added_at"] is not None
    assert job["dataset_removed_at"] is None
    assert object_exists(aws, retention_key(job_id))


def test_an_upload_after_the_dataset_was_removed_is_409_dataset_removed(
    aws, db, pg, make_cfg, tmp_path
):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db, consent=True, done=True)
    pg.conn.execute("UPDATE jobs SET dataset_removed_at = now() WHERE job_id = %s::uuid", (job_id,))

    resp = client.post(f"/api/jobs/{job_id}/retention", json={"report": valid_report()})

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "dataset_removed"
    assert pg.job(job_id)["retention_added_at"] is None
    assert not object_exists(aws, retention_key(job_id))


# ---------------------------------------------------------------- needs_retention


def test_list_jobs_marks_only_done_consented_jobs_without_a_curve_or_removal(db, pg):
    needed, _ = make_job(db, consent=True, done=True)
    with_curve, _ = make_job(db, consent=True, done=True)
    pg.conn.execute(
        "UPDATE jobs SET retention_added_at = now() WHERE job_id = %s::uuid", (with_curve,)
    )
    removed, _ = make_job(db, consent=True, done=True)
    pg.conn.execute(
        "UPDATE jobs SET dataset_removed_at = now() WHERE job_id = %s::uuid", (removed,)
    )
    queued, _ = make_job(db, consent=True, done=False)
    unconsented, _ = make_job(db, consent=False, done=True)
    failed, _ = make_job(db, consent=True, done=False)
    assert billing.issue_refund(db, failed, "processing_failed", queued_before_s=0) is True

    rows = billing.list_jobs(db, WEB_USER_ID, 50)
    by_id = {str(row["job_id"]): row["needs_retention"] for row in rows}

    assert by_id[needed] is True
    for job_id in (with_curve, removed, queued, unconsented, failed):
        assert by_id[job_id] is False


def test_the_jobs_api_exposes_needs_retention(aws, db, pg, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    needed, _ = make_job(db, consent=True, done=True)
    with_curve, _ = make_job(db, consent=True, done=True)
    pg.conn.execute(
        "UPDATE jobs SET retention_added_at = now() WHERE job_id = %s::uuid", (with_curve,)
    )

    resp = client.get("/api/jobs")

    assert resp.status_code == 200
    by_id = {row["job_id"]: row["needs_retention"] for row in resp.get_json()}
    assert by_id[needed] is True
    assert by_id[with_curve] is False
