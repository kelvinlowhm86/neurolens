"""M3i web changes (docs/M3i_spec.md §2, §6-§9, §11).

The retention route needs a valid `video_url` and `same_cut`, parses a Facebook report with the
link's video id, and saves the link and its source. The invite credit and the 7-day curve reward
are read from `config.json`, and `grant_credit.py` grants the invite credit when no amount is
given. Refusals write nothing to S3 or PostgreSQL.
"""

import importlib.util
import json
import re
import uuid
from datetime import timedelta

import pytest
from conftest import ISSUER, REPO_ROOT, WEB_USER_ID
from neurolens import billing, pricing, settings, storage
from neurolens.web.app import create_app

STARTER_CENTS = 10_000
OTHER_USER = "other-user"
VIEWS = "lt_1k"
PUBLISHED = "week"
REWARD_MIN_DAYS = 7
DAY_S = 24 * 60 * 60
YOUTUBE_ID = "dQw4w9WgXcQ"
YOUTUBE_LINK = f"https://www.youtube.com/watch?v={YOUTUBE_ID}"
YOUTUBE_REF = f"youtube:{YOUTUBE_ID}"
FACEBOOK_ID = "1234567890"
FACEBOOK_LINK = f"https://www.facebook.com/MyPage/videos/{FACEBOOK_ID}"
FACEBOOK_REF = f"facebook:{FACEBOOK_ID}"
MISSING = object()

SCRIPT = REPO_ROOT / "infra" / "grant_credit.py"
_spec = importlib.util.spec_from_file_location("m3i_grant_credit", SCRIPT)
grant_credit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(grant_credit)


# ---------------------------------------------------------------- fixtures and helpers


def report_text(points):
    return json.dumps(
        {
            "kind": "youtubeAnalytics#resultTable",
            "columnHeaders": [
                {"name": "elapsedVideoTimeRatio", "columnType": "DIMENSION", "dataType": "FLOAT"},
                {"name": "audienceWatchRatio", "columnType": "METRIC", "dataType": "FLOAT"},
            ],
            "rows": [[elapsed, watch] for elapsed, watch in points],
        }
    )


def curve(points=20, *, first=1.0, last=0.5):
    return [(i / (points - 1), first + (last - first) * i / (points - 1)) for i in range(points)]


def facebook_values():
    return {str(i): 1.0 - 0.5 * i / 39 for i in range(40)}


def facebook_report(video_id=FACEBOOK_ID):
    return json.dumps(
        {
            "data": [
                {
                    "name": "total_video_retention_graph",
                    "period": "lifetime",
                    "id": f"{video_id}/video_insights",
                    "values": [{"value": facebook_values()}],
                }
            ]
        }
    )


def video_ref_for(job_id):
    return f"youtube:{job_id.replace('-', '')[:11]}"


def curve_args(job_id, *, sha="a" * 64, points=None, video_ref=None):
    snapshot_id = str(uuid.uuid4())
    return {
        "snapshot_id": snapshot_id,
        "raw_key": f"dataset/{job_id}/retention/{snapshot_id}.json",
        "csv_key": f"dataset/{job_id}/retention/{snapshot_id}.csv",
        "curve_sha256": sha,
        "points": points if points is not None else curve(),
        "quality_flags": [],
        "views": VIEWS,
        "published": PUBLISHED,
        "promoted": False,
        "reward_cap": 5,
        "reward_window_days": 30,
        "video_ref": video_ref if video_ref is not None else video_ref_for(job_id),
        "same_cut": "same",
        "source": "youtube_api",
        "reward_min_days": REWARD_MIN_DAYS,
        "facts": None,
        "length_tolerance_s": 2,
    }


def make_job(
    db,
    *,
    user_id=WEB_USER_ID,
    state="done",
    consent=True,
    seconds=27.4,
    sha="a" * 64,
    starter_cents=STARTER_CENTS,
):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", starter_cents)
    job_id = str(uuid.uuid4())
    key = storage.object_key(user_id, job_id, "video/mp4")
    billing.reserve(
        db, user_id, job_id, key, "clip.mp4", round(seconds * 1000), research_consent=consent
    )
    if state in ("processing", "done"):
        assert billing.claim(db, job_id) == 1
    if state == "done":
        assert billing.settle_success(db, job_id) is True
    if sha is not None and state != "queued":
        billing.record_video_hash(db, job_id, sha)
    return job_id, key


def snapshot_rows(pg, job_id):
    return pg.rows(
        "SELECT * FROM retention_snapshots WHERE job_id = %s::uuid ORDER BY added_at", job_id
    )


def reward_date(pg, job_id, days=REWARD_MIN_DAYS):
    return storage.utc_text(pg.job(job_id)["created_at"] + timedelta(days=days))


def app_for(aws, db, make_cfg, tmp_path):
    cfg = make_cfg(
        research={
            "curve_rewards_per_window": 5,
            "curve_reward_window_days": 30,
            "curve_reward_min_days": REWARD_MIN_DAYS,
            "no_curve_days": 90,
        }
    )
    app = create_app(
        cfg=cfg,
        data_dir=tmp_path / "data",
        db=db,
        s3_client=aws.s3,
    )
    app.config["TESTING"] = True
    return app.test_client()


def retention_body(**overrides):
    body = {
        "report": report_text(curve()),
        "video_url": YOUTUBE_LINK,
        "same_cut": "same",
        "views": VIEWS,
        "published": PUBLISHED,
        "promoted": False,
    }
    body.update(overrides)
    return body


def retention_keys(aws, job_id):
    prefix = f"dataset/{job_id}/retention/"
    return aws.s3.list_objects_v2(Bucket=aws.bucket, Prefix=prefix).get("Contents", [])


# ---------------------------------------------------------------- the route's new fields


@pytest.fixture(autouse=True)
def youtube_falls_back(monkeypatch):
    """M3j: the save route looks the link up; these tests keep the unknown-facts fallback."""
    try:
        from neurolens import youtube
    except ImportError:
        return

    def failed(*args, **kwargs):
        raise youtube.LookupFailed("test fallback")

    monkeypatch.setattr(youtube, "lookup", failed)


def test_a_youtube_curve_saves_its_link_and_same_cut_answer(db, aws, make_cfg, tmp_path, pg):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)

    resp = client.post(f"/api/jobs/{job_id}/retention", json=retention_body())

    assert resp.status_code == 200
    [snapshot] = snapshot_rows(pg, job_id)
    assert snapshot["video_ref"] == YOUTUBE_REF
    assert snapshot["same_cut"] == "same"
    assert snapshot["source"] == "youtube_api"


def test_a_facebook_curve_saves_with_the_facebook_source(db, aws, make_cfg, tmp_path, pg):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)

    resp = client.post(
        f"/api/jobs/{job_id}/retention",
        json=retention_body(
            report=facebook_report(),
            video_url=FACEBOOK_LINK,
            same_cut="light_edit",
        ),
    )

    assert resp.status_code == 200
    [snapshot] = snapshot_rows(pg, job_id)
    assert snapshot["video_ref"] == FACEBOOK_REF
    assert snapshot["same_cut"] == "light_edit"
    assert snapshot["source"] == "facebook_api"


def test_a_day_six_curve_saves_without_a_reward_and_returns_the_reward_date(
    db, aws, make_cfg, tmp_path, pg
):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
    pg.age(job_id, created_s=6 * DAY_S)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)

    resp = client.post(f"/api/jobs/{job_id}/retention", json=retention_body())

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["rewarded_cents"] == 0
    assert data["reward_from"] == reward_date(pg, job_id)
    [snapshot] = snapshot_rows(pg, job_id)
    assert snapshot["rewarded_cents"] == 0


@pytest.mark.parametrize("same_cut", ["same", "light_edit", "recut"])
def test_every_published_video_answer_is_accepted(db, aws, make_cfg, tmp_path, pg, same_cut):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)

    resp = client.post(f"/api/jobs/{job_id}/retention", json=retention_body(same_cut=same_cut))

    assert resp.status_code == 200
    [snapshot] = snapshot_rows(pg, job_id)
    assert snapshot["same_cut"] == same_cut


def test_the_link_is_checked_before_the_report(db, aws, make_cfg, tmp_path, pg):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)

    resp = client.post(
        f"/api/jobs/{job_id}/retention",
        json=retention_body(video_url="https://vimeo.com/123456789", report="not json"),
    )

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_video_link"
    assert snapshot_rows(pg, job_id) == []
    assert retention_keys(aws, job_id) == []


# ---------------------------------------------------------------- refused input


def test_an_unrecognised_link_is_refused_with_its_message(db, aws, make_cfg, tmp_path, pg):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)

    resp = client.post(
        f"/api/jobs/{job_id}/retention",
        json=retention_body(video_url="https://vimeo.com/123456789"),
    )

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_video_link"
    assert resp.get_json()["message"] == "Paste the video's YouTube or Facebook link."
    assert snapshot_rows(pg, job_id) == []
    assert retention_keys(aws, job_id) == []


@pytest.mark.parametrize(
    "change",
    [
        {"video_url": MISSING},
        {"video_url": None},
        {"video_url": 123},
        {"video_url": ""},
        {"same_cut": MISSING},
        {"same_cut": None},
        {"same_cut": "different"},
    ],
    ids=[
        "video_url_missing",
        "video_url_null",
        "video_url_number",
        "video_url_empty",
        "same_cut_missing",
        "same_cut_null",
        "same_cut_wrong",
    ],
)
def test_a_missing_or_bad_link_or_answer_is_400_and_stores_nothing(
    db, aws, make_cfg, tmp_path, pg, change
):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
    body = retention_body()
    for key, value in change.items():
        if value is MISSING:
            body.pop(key)
        else:
            body[key] = value

    resp = client.post(f"/api/jobs/{job_id}/retention", json=body)

    assert resp.status_code == 400
    assert snapshot_rows(pg, job_id) == []
    assert retention_keys(aws, job_id) == []


def test_a_facebook_report_for_another_video_is_refused(db, aws, make_cfg, tmp_path, pg):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)

    resp = client.post(
        f"/api/jobs/{job_id}/retention",
        json=retention_body(
            report=facebook_report(video_id="9999999999"),
            video_url=FACEBOOK_LINK,
        ),
    )

    assert resp.status_code == 400
    assert resp.get_json()["message"] == "This report is for a different video than the link."
    assert snapshot_rows(pg, job_id) == []
    assert retention_keys(aws, job_id) == []


def test_a_link_taken_by_another_account_is_409_video_link_taken(db, aws, make_cfg, tmp_path, pg):
    client = app_for(aws, db, make_cfg, tmp_path)
    other_job, _ = make_job(db, user_id=OTHER_USER)
    pg.age(other_job, created_s=8 * DAY_S)
    billing.grant_credit(db, f"{OTHER_USER}@example.com", 100)
    billing.add_curve(db, OTHER_USER, other_job, **curve_args(other_job, video_ref=YOUTUBE_REF))
    job_id, _ = make_job(db)

    resp = client.post(f"/api/jobs/{job_id}/retention", json=retention_body())

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "video_link_taken"
    assert resp.get_json()["message"] == "This video is linked to another account."
    assert snapshot_rows(pg, job_id) == []
    assert retention_keys(aws, job_id) == []


def test_a_second_link_on_a_job_is_409_video_link_changed(db, aws, make_cfg, tmp_path, pg):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
    billing.add_curve(db, WEB_USER_ID, job_id, **curve_args(job_id, video_ref=FACEBOOK_REF))

    resp = client.post(f"/api/jobs/{job_id}/retention", json=retention_body())

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "video_link_changed"
    assert resp.get_json()["message"] == "This analysis is already linked to another video."
    assert len(snapshot_rows(pg, job_id)) == 1
    assert retention_keys(aws, job_id) == []


# ---------------------------------------------------------------- config and copy


def test_config_has_the_invite_credit_and_the_seven_day_curve_reward():
    cfg = settings.load_settings()

    assert cfg["billing"]["invite_cents"] == 1000
    assert cfg["billing"]["starter_cents"] == 0
    assert cfg["research"]["curve_reward_min_days"] == REWARD_MIN_DAYS


def test_the_landing_page_offers_five_minutes_from_the_configured_credit():
    cfg = settings.load_settings()
    minutes = (
        cfg["billing"]["invite_cents"] * pricing.BLOCK_SECONDS / (pricing.CENTS_PER_BLOCK * 60)
    )
    assert minutes == 5

    html = (REPO_ROOT / "static" / "index.html").read_text()
    text = " ".join(re.sub(r"<[^>]+>", " ", html).split())

    assert (
        "5 minutes of free analysis for invited creators, marketers, teachers and researchers."
        in text
    )
    assert "add its retention curve once it's been public a week" in text


def _section_text(html, anchor):
    start = re.search(rf'<h2[^>]*id="{anchor}"[^>]*>', html)
    assert start, f'the page has no <h2 id="{anchor}"> section'
    rest = html[start.end() :]
    end = re.search(r"<h2\b", rest)
    section = rest[: end.start()] if end else rest
    return " ".join(re.sub(r"<[^>]+>", " ", section).split())


def test_the_privacy_page_states_the_configured_seven_day_rule():
    days = settings.load_settings()["research"]["curve_reward_min_days"]
    assert days == REWARD_MIN_DAYS

    html = (REPO_ROOT / "static" / "privacy.html").read_text()
    text = _section_text(html, "sharing")

    assert f"public at least {days} days" in text
    assert "YouTube or Facebook" in text


# ---------------------------------------------------------------- grant_credit.py


def _answers(*replies):
    prompts = []
    replies = iter(replies)

    def ask(prompt):
        prompts.append(prompt)
        return next(replies)

    return ask, prompts


def test_grant_without_an_amount_gives_the_invite_credit(db, pg):
    user_id = billing.sign_in(db, ISSUER, "sub-invite", "ann@example.com", 0)
    assert settings.load_settings()["billing"]["invite_cents"] == 1000
    ask, prompts = _answers("ann@example.com", "y")

    assert grant_credit.main(["--backend", "postgres"], ask=ask, db=db) == 0

    assert pg.balance(user_id) == (1000, 0)
    assert "Grant $10.00 (5 min, the invite credit) to ann@example.com?" in prompts[1]
