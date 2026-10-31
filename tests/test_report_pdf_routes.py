"""M3h §18f: the two PDF routes.

Signed out is 401, another user's job or pair is 404, no written report is 409 no_report, and the
attachment name is the CSV's shape with its own suffix. Everything is faked: moto S3 and the
test PostgreSQL.
"""

import json
import urllib.parse

import pytest
from conftest import USER_ID, WEB_USER_ID
from neurolens import storage
from test_m3g_report import app_for, done_job, job_in_state, put_result, set_report
from test_m3h_routes import comparison_key, done_pair, set_comparison


def put_report(aws, job_id, report):
    aws.s3.put_object(
        Bucket=aws.bucket,
        Key=f"results/{job_id}.report.json",
        Body=json.dumps(report).encode(),
    )


def put_comparison_report(aws, a, b, report):
    aws.s3.put_object(
        Bucket=aws.bucket,
        Key=comparison_key(a, b),
        Body=json.dumps(report).encode(),
    )


def single_report():
    return {
        "headline": "A written headline",
        "summary": "A written summary.",
        "moments": [
            {
                "start": 0,
                "end": 1,
                "what_happens": "Something happens.",
                "brain_response": "The response rises.",
                "suggestion": None,
            }
        ],
        "strengths": ["A strength."],
        "improvements": ["An improvement."],
        "created_at": "2026-10-31T00:22:47Z",
    }


def comparison_report():
    return {
        "headline": "A written comparison",
        "summary": "A written comparison summary.",
        "differences": [
            {
                "a": {"start": 0, "end": 1},
                "b": {"start": 0, "end": 1},
                "what_differs": "A differs here.",
                "brain_response": "A rises more.",
            }
        ],
        "a_does_better": ["A opens faster."],
        "b_does_better": ["B settles sooner."],
        "suggestions": ["Keep A's opening."],
        "created_at": "2026-10-31T00:23:37Z",
    }


def ready_job(aws, db, pg, *, user_id=WEB_USER_ID, filename=None):
    job_id, _ = job_in_state(db, "done", user_id=user_id)
    put_result(aws, job_id, frames=3)
    set_report(pg, job_id, "ready", tries=1)
    put_report(aws, job_id, single_report())
    if filename is not None:
        pg.conn.execute("UPDATE jobs SET filename = %s WHERE job_id = %s::uuid", (filename, job_id))
    return job_id


def full_name(header):
    encoded = header.split("filename*=UTF-8''", 1)[1].split(";", 1)[0].strip()
    return urllib.parse.unquote(encoded)


def fallback_name(header):
    return header.split('filename="', 1)[1].split('"', 1)[0]


def test_a_ready_report_downloads_as_a_pdf_named_after_the_video(aws, db, pg, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id = ready_job(aws, db, pg, filename="My Ad.mp4")

    resp = client.get(f"/api/jobs/{job_id}/report.pdf")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/pdf"
    assert resp.data.startswith(b"%PDF")
    header = resp.headers["Content-Disposition"]
    header.encode("latin-1")
    assert full_name(header) == "My Ad \u2013 NeuroLens report.pdf"
    assert fallback_name(header) == "My Ad - NeuroLens report.pdf"


def test_a_missing_frames_object_still_gives_a_pdf(aws, db, pg, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id = ready_job(aws, db, pg)

    resp = client.get(f"/api/jobs/{job_id}/report.pdf")

    assert resp.status_code == 200
    assert resp.data.startswith(b"%PDF")


@pytest.mark.parametrize("state", [None, "writing", "failed"])
def test_a_job_without_a_written_report_is_409_no_report(aws, db, pg, make_cfg, tmp_path, state):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = job_in_state(db, "done", user_id=WEB_USER_ID)
    put_result(aws, job_id, frames=3)
    if state is not None:
        set_report(pg, job_id, state, tries=1)

    resp = client.get(f"/api/jobs/{job_id}/report.pdf")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "no_report"


def test_a_ready_state_without_a_stored_report_is_409_no_report(aws, db, pg, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = job_in_state(db, "done", user_id=WEB_USER_ID)
    put_result(aws, job_id, frames=3)
    set_report(pg, job_id, "ready", tries=1)

    resp = client.get(f"/api/jobs/{job_id}/report.pdf")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "no_report"


def test_another_users_job_is_404(aws, db, pg, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id = ready_job(aws, db, pg, user_id=USER_ID)

    resp = client.get(f"/api/jobs/{job_id}/report.pdf")

    assert resp.status_code == 404


def test_a_ready_comparison_downloads_as_a_pdf(aws, db, pg, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    a, b = done_pair(aws, db)
    set_comparison(pg, a, b, "ready", tries=1)
    put_comparison_report(aws, a, b, comparison_report())
    pg.conn.execute(
        "UPDATE jobs SET filename = CASE job_id "
        "WHEN %s::uuid THEN 'Ad A.mp4' ELSE 'Ad B.mp4' END "
        "WHERE job_id IN (%s::uuid, %s::uuid)",
        (a, a, b),
    )

    resp = client.get(f"/api/compare/{b}/{a}/report.pdf")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/pdf"
    assert resp.data.startswith(b"%PDF")
    header = resp.headers["Content-Disposition"]
    header.encode("latin-1")
    assert full_name(header) == "Ad A vs Ad B \u2013 NeuroLens comparison.pdf"
    assert fallback_name(header) == "Ad A vs Ad B - NeuroLens comparison.pdf"


@pytest.mark.parametrize("state", [None, "writing", "failed"])
def test_a_pair_without_a_written_comparison_is_409_no_report(
    aws, db, pg, make_cfg, tmp_path, state
):
    client = app_for(aws, db, make_cfg, tmp_path)
    a, b = done_pair(aws, db)
    if state is not None:
        set_comparison(pg, a, b, state, tries=1)

    resp = client.get(f"/api/compare/{a}/{b}/report.pdf")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "no_report"


def test_a_ready_comparison_state_without_a_stored_report_is_409_no_report(
    aws, db, pg, make_cfg, tmp_path
):
    client = app_for(aws, db, make_cfg, tmp_path)
    a, b = done_pair(aws, db)
    set_comparison(pg, a, b, "ready", tries=1)

    resp = client.get(f"/api/compare/{a}/{b}/report.pdf")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "no_report"


def test_another_users_pair_is_404(aws, db, pg, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    first, _ = job_in_state(db, "done", user_id=USER_ID)
    second, _ = job_in_state(db, "done", user_id=USER_ID)
    a, b = storage.comparison_pair(first, second)
    put_result(aws, a, frames=3)
    put_result(aws, b, frames=3)
    set_comparison(pg, a, b, "ready", tries=1)
    put_comparison_report(aws, a, b, comparison_report())

    resp = client.get(f"/api/compare/{a}/{b}/report.pdf")

    assert resp.status_code == 404


def test_the_same_video_is_400_same_video(aws, db, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = done_job(db, user_id=WEB_USER_ID)
    put_result(aws, job_id, frames=3)

    resp = client.get(f"/api/compare/{job_id}/{job_id}/report.pdf")

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "same_video"


def test_a_signed_out_visitor_gets_401_on_both_routes(aws, db, pg, cognito_app):
    a, b = done_pair(aws, db)
    set_comparison(pg, a, b, "ready", tries=1)
    visitor = cognito_app().test_client()

    assert visitor.get(f"/api/jobs/{a}/report.pdf").status_code == 401
    assert visitor.get(f"/api/compare/{a}/{b}/report.pdf").status_code == 401
