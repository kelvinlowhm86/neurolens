"""M3h §4, §13: the compare routes.

Both orders of the ids are one pair, every refusal has its code, a failed start gives the try
back, GET serves the stored answer, and a signed-out visitor cannot reach Bedrock.
"""

import json
import uuid

import pytest
from conftest import STARTER_CENTS, WEB_USER_ID
from neurolens import billing
from neurolens.web.app import create_app
from test_m3g_report import job_in_state, put_result

REPORT = {"max_tries": 3, "stale_minutes": 5}
MAX_TRIES = REPORT["max_tries"]
STALE_MINUTES = REPORT["stale_minutes"]

LOW = "00000000-0000-4000-8000-000000000001"
HIGH = "ffffffff-ffff-4fff-8fff-ffffffffffff"


def report_cfg(**overrides):
    return {**REPORT, **overrides}


def canonical(x, y):
    return tuple(sorted(str(uuid.UUID(str(value))) for value in (x, y)))


def comparison_key(a, b):
    return f"results/{a}_{b}.report.json"


class Starter:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def __call__(self, event):
        self.calls.append(event)
        if self.error is not None:
            raise self.error


def app_for(aws, db, make_cfg, tmp_path, starter=None):
    app = create_app(
        cfg=make_cfg(report=report_cfg()),
        data_dir=tmp_path / "data",
        db=db,
        s3_client=aws.s3,
        report_starter=starter,
    )
    app.config["TESTING"] = True
    return app.test_client()


def job_with_id(aws, db, job_id, state="done", *, user_id=WEB_USER_ID, frames=3):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", STARTER_CENTS)
    key = f"uploads/{user_id}/{job_id}.mp4"
    billing.reserve(db, user_id, job_id, key, "clip.mp4", 3000)
    if state in ("processing", "done", "failed"):
        assert billing.claim(db, job_id) == 1
    if state == "done":
        assert billing.settle_success(db, job_id) is True
        put_result(aws, job_id, frames=frames)
    elif state == "failed":
        assert billing.issue_refund(db, job_id, "stalled", attempt=1) is True
    return job_id


def done_job(aws, db, *, user_id=WEB_USER_ID, frames=3):
    job_id, _ = job_in_state(db, "done", user_id=user_id)
    put_result(aws, job_id, frames=frames)
    return job_id


def done_pair(aws, db, *, frames=(3, 3)):
    return canonical(done_job(aws, db, frames=frames[0]), done_job(aws, db, frames=frames[1]))


def set_comparison(pg, a, b, state="writing", *, tries=1, started_minutes_ago=0, error=None):
    pg.conn.execute(
        "INSERT INTO comparisons (job_a, job_b, state, tries, started_at, error) "
        "VALUES (%s::uuid, %s::uuid, %s, %s, now() - make_interval(mins => %s), %s) "
        "ON CONFLICT (job_a, job_b) DO UPDATE SET state = EXCLUDED.state, "
        "tries = EXCLUDED.tries, started_at = EXCLUDED.started_at, error = EXCLUDED.error",
        (a, b, state, tries, started_minutes_ago, error),
    )


def comparison(pg, a, b):
    return pg.one("SELECT * FROM comparisons WHERE job_a = %s::uuid AND job_b = %s::uuid", a, b)


# ---------------------------------------------------------------- POST


def test_post_starts_a_comparison_with_the_canonical_pair(aws, db, pg, make_cfg, tmp_path):
    starter = Starter()
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    a, b = done_pair(aws, db)

    resp = client.post(f"/api/compare/{a}/{b}/report")

    assert resp.status_code == 202
    assert resp.get_json() == {"report_state": "writing"}
    assert starter.calls == [{"pair": [a, b]}]
    row = comparison(pg, a, b)
    assert (row["state"], row["tries"]) == ("writing", 1)


def test_post_b_vs_a_is_the_same_pair_and_started_only_once(aws, db, pg, make_cfg, tmp_path):
    starter = Starter()
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    a, b = done_pair(aws, db)
    assert client.post(f"/api/compare/{a}/{b}/report").status_code == 202

    resp = client.post(f"/api/compare/{b}/{a}/report")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "report_writing"
    assert starter.calls == [{"pair": [a, b]}]
    assert comparison(pg, a, b)["tries"] == 1


def test_post_the_same_video_twice_is_400_same_video(aws, db, make_cfg, tmp_path):
    starter = Starter()
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    job_id = done_job(aws, db)

    resp = client.post(f"/api/compare/{job_id}/{job_id}/report")

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "same_video"
    assert starter.calls == []


@pytest.mark.parametrize("foreign", ["a", "b"])
def test_post_either_job_not_owned_is_404(aws, db, make_cfg, tmp_path, foreign):
    starter = Starter()
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    if foreign == "a":
        job_with_id(aws, db, LOW, user_id="someone-else")
        job_with_id(aws, db, HIGH)
    else:
        job_with_id(aws, db, LOW)
        job_with_id(aws, db, HIGH, user_id="someone-else")

    resp = client.post(f"/api/compare/{LOW}/{HIGH}/report")

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"
    assert starter.calls == []


@pytest.mark.parametrize("unfinished", ["a", "b"])
@pytest.mark.parametrize("state", ["queued", "processing", "failed"])
def test_post_either_job_not_done_is_404(aws, db, make_cfg, tmp_path, unfinished, state):
    starter = Starter()
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    if unfinished == "a":
        job_with_id(aws, db, LOW, state)
        job_with_id(aws, db, HIGH)
    else:
        job_with_id(aws, db, LOW)
        job_with_id(aws, db, HIGH, state)

    resp = client.post(f"/api/compare/{LOW}/{HIGH}/report")

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"
    assert starter.calls == []


@pytest.mark.parametrize("without", ["a", "b"])
def test_post_either_job_without_frames_is_409_no_frames(aws, db, make_cfg, tmp_path, without):
    starter = Starter()
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    if without == "a":
        job_with_id(aws, db, LOW, frames=None)
        job_with_id(aws, db, HIGH)
    else:
        job_with_id(aws, db, LOW)
        job_with_id(aws, db, HIGH, frames=None)

    resp = client.post(f"/api/compare/{LOW}/{HIGH}/report")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "no_frames"
    assert starter.calls == []


def test_post_a_ready_comparison_is_409_report_ready(aws, db, pg, make_cfg, tmp_path):
    starter = Starter()
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    a, b = done_pair(aws, db)
    set_comparison(pg, a, b, "ready", tries=1)

    resp = client.post(f"/api/compare/{a}/{b}/report")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "report_ready"
    assert starter.calls == []


def test_post_a_fresh_writing_comparison_is_409_report_writing(aws, db, pg, make_cfg, tmp_path):
    starter = Starter()
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    a, b = done_pair(aws, db)
    set_comparison(pg, a, b, "writing", tries=1, started_minutes_ago=0)

    resp = client.post(f"/api/compare/{a}/{b}/report")

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "report_writing"
    assert starter.calls == []


def test_post_reclaims_a_stale_writing_comparison(aws, db, pg, make_cfg, tmp_path):
    starter = Starter()
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    a, b = done_pair(aws, db)
    set_comparison(pg, a, b, "writing", tries=1, started_minutes_ago=STALE_MINUTES + 1)

    resp = client.post(f"/api/compare/{a}/{b}/report")

    assert resp.status_code == 202
    assert starter.calls == [{"pair": [a, b]}]
    assert comparison(pg, a, b)["tries"] == 2


def test_post_with_no_tries_left_is_429(aws, db, pg, make_cfg, tmp_path):
    starter = Starter()
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    a, b = done_pair(aws, db)
    set_comparison(pg, a, b, "failed", tries=MAX_TRIES, error="bad_answer")

    resp = client.post(f"/api/compare/{a}/{b}/report")

    assert resp.status_code == 429
    assert resp.get_json()["error"] == "no_tries_left"
    assert starter.calls == []


def test_post_start_failure_is_503_and_returns_the_try(aws, db, pg, make_cfg, tmp_path):
    starter = Starter(RuntimeError("lambda unavailable"))
    client = app_for(aws, db, make_cfg, tmp_path, starter)
    a, b = done_pair(aws, db)

    resp = client.post(f"/api/compare/{a}/{b}/report")

    assert resp.status_code == 503
    assert resp.get_json()["error"] == "report_failed"
    row = comparison(pg, a, b)
    assert (row["state"], row["tries"]) == ("failed", 0)


# ---------------------------------------------------------------- GET


def test_get_with_no_row_answers_all_tries_left(aws, db, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    a, b = done_pair(aws, db)

    for x, y in ((a, b), (b, a)):
        resp = client.get(f"/api/compare/{x}/{y}/report")

        data = resp.get_json()
        assert resp.status_code == 200
        assert (data["a"], data["b"]) == (a, b)
        assert data["report_state"] is None
        assert data["tries_left"] == MAX_TRIES
        assert data["report"] is None


def test_get_serves_a_ready_comparison_both_ways_round(aws, db, pg, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    a, b = done_pair(aws, db)
    stored = {
        "headline": "A beats B",
        "summary": "A draws the stronger response.",
        "differences": [],
        "a_does_better": ["A 0-1 s"],
        "b_does_better": [],
        "suggestions": [],
    }
    aws.s3.put_object(
        Bucket=aws.bucket,
        Key=comparison_key(a, b),
        Body=json.dumps(stored).encode(),
    )
    set_comparison(pg, a, b, "ready", tries=1)

    for x, y in ((a, b), (b, a)):
        data = client.get(f"/api/compare/{x}/{y}/report").get_json()

        assert (data["a"], data["b"]) == (a, b)
        assert data["report_state"] == "ready"
        assert data["tries_left"] == MAX_TRIES - 1
        assert data["report"] == stored


def test_get_reads_a_stale_writing_comparison_as_failed(aws, db, pg, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    a, b = done_pair(aws, db)
    set_comparison(pg, a, b, "writing", tries=1, started_minutes_ago=STALE_MINUTES + 1)

    data = client.get(f"/api/compare/{a}/{b}/report").get_json()

    assert data["report_state"] == "failed"
    assert data["report"] is None


@pytest.mark.parametrize("foreign", ["a", "b"])
def test_get_either_job_not_owned_is_404(aws, db, make_cfg, tmp_path, foreign):
    client = app_for(aws, db, make_cfg, tmp_path)
    if foreign == "a":
        job_with_id(aws, db, LOW, user_id="someone-else")
        job_with_id(aws, db, HIGH)
    else:
        job_with_id(aws, db, LOW)
        job_with_id(aws, db, HIGH, user_id="someone-else")

    resp = client.get(f"/api/compare/{LOW}/{HIGH}/report")

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"


def test_a_signed_out_visitor_cannot_start_a_comparison(aws, db, make_cfg, tmp_path, cognito_app):
    signed_in = app_for(aws, db, make_cfg, tmp_path, Starter())
    a, b = done_pair(aws, db)
    assert signed_in.post(f"/api/compare/{a}/{b}/report").status_code == 202

    visitor = cognito_app().test_client()

    assert visitor.post(f"/api/compare/{a}/{b}/report").status_code == 401
    assert visitor.get(f"/api/compare/{a}/{b}/report").status_code == 401
