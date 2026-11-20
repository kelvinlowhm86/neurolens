"""One curve rule.

A curve pays when every other rule passes and the checked video has at least 1,000 views. There
is no one-week wait any more: a young video with enough views pays at once, and a hidden or
unknown count skips the views check as before. No answer carries a reward date, and the old
`curve_reward_min_days` setting is gone. Money and snapshots are read directly from PostgreSQL.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import RESEARCH, REWARD, WEB_USER_ID
from neurolens import billing, pricing, storage
from neurolens.web.app import create_app

STARTER_CENTS = 10_000
PRICE_CENTS = pricing.estimate_cost_cents(30.1)
LENGTH_TOLERANCE_S = RESEARCH["curve_length_tolerance_s"]
MIN_VIEWS = RESEARCH["curve_min_views"]
VIEWS = "lt_1k"
PUBLISHED = "week"
DAY_S = 24 * 60 * 60
YOUTUBE_ID = "dQw4w9WgXcQ"
YOUTUBE_LINK = f"https://www.youtube.com/watch?v={YOUTUBE_ID}"
YOUTUBE_REF = f"youtube:{YOUTUBE_ID}"


def curve(points=20, *, first=1.0, last=0.5):
    return [(i / (points - 1), first + (last - first) * i / (points - 1)) for i in range(points)]


def facts(published_at, *, duration_s=31, views=25048):
    from neurolens.youtube import VideoFacts

    return VideoFacts(published_at=published_at, duration_s=duration_s, views=views)


def now():
    return datetime.now(UTC)


def days_ago(days):
    return datetime.now(UTC) - timedelta(days=days)


def video_ref_for(job_id):
    return f"youtube:{job_id.replace('-', '')[:11]}"


def make_job(db, *, user_id=WEB_USER_ID, seconds=30.1, sha="a" * 64):
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


def curve_args(
    job_id,
    *,
    sha="a" * 64,
    points=None,
    video_ref=None,
    facts_=None,
    views=None,
    published=None,
    min_views=MIN_VIEWS,
):
    snapshot_id = str(uuid.uuid4())
    return {
        "snapshot_id": snapshot_id,
        "raw_key": f"dataset/{job_id}/retention/{snapshot_id}.json",
        "csv_key": f"dataset/{job_id}/retention/{snapshot_id}.csv",
        "curve_sha256": sha,
        "points": points if points is not None else curve(),
        "quality_flags": [],
        "views": views,
        "published": published,
        "promoted": False,
        **REWARD,
        "video_ref": video_ref if video_ref is not None else video_ref_for(job_id),
        "same_cut": "same",
        "source": "youtube_api",
        "facts": facts_,
        "length_tolerance_s": LENGTH_TOLERANCE_S,
        "min_views": min_views,
    }


def snapshot_rows(pg, job_id):
    return pg.rows(
        "SELECT * FROM retention_snapshots WHERE job_id = %s::uuid ORDER BY added_at", job_id
    )


def offer(db, user_id, job_id, *, video_ref, facts_, min_views=MIN_VIEWS):
    args = {
        **REWARD,
        "length_tolerance_s": LENGTH_TOLERANCE_S,
        "min_views": min_views,
    }
    cents, reason = billing.curve_offer(db, user_id, job_id, video_ref, facts_, **args)
    return cents, reason


def patch_lookup(monkeypatch, result):
    from neurolens import youtube

    monkeypatch.setattr(youtube, "lookup", lambda video_id, api_key, *, timeout=5: result)


def app_client(aws, db, make_cfg, tmp_path, *, curve_min_views=MIN_VIEWS):
    cfg = make_cfg(
        research={"curve_min_views": curve_min_views}, youtube_api_key="test-youtube-key"
    )
    app = create_app(
        cfg=cfg,
        data_dir=tmp_path / "data",
        db=db,
        s3_client=aws.s3,
    )
    app.config["TESTING"] = True
    return app.test_client()


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


def reward_case(name):
    """A job age, its facts and the curve hash for one check-versus-save case."""
    cases = {
        "pays_now": (0, facts(now() - timedelta(days=30), views=25048), "a" * 64),
        "exactly_the_minimum": (0, facts(now() - timedelta(days=30), views=MIN_VIEWS), "b" * 64),
        "too_few_views": (0, facts(now() - timedelta(days=30), views=999), "c" * 64),
        "too_few_views_on_a_young_video": (
            10 * DAY_S,
            facts(now() - timedelta(days=2), views=640),
            "d" * 64,
        ),
        "hidden_views": (0, facts(now() - timedelta(days=30), views=None), "e" * 64),
        "unknown_facts": (0, None, "f" * 64),
        "few_views_with_a_length_mismatch": (
            0,
            facts(now() - timedelta(days=30), duration_s=33, views=640),
            "1" * 64,
        ),
    }
    return cases[name]


def test_a_young_curve_with_a_thousand_views_pays_at_once(db, pg):
    job_id = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)

    result = billing.add_curve(
        db,
        WEB_USER_ID,
        job_id,
        **curve_args(job_id, facts_=facts(now(), views=MIN_VIEWS)),
    )

    assert result["rewarded_cents"] == PRICE_CENTS
    assert result["reason"] is None
    assert pg.job(job_id)["curve_rewarded_at"] is not None
    assert pg.kinds(WEB_USER_ID).count("curve_reward") == 1
    [snapshot] = snapshot_rows(pg, job_id)
    assert snapshot["checked_views"] == MIN_VIEWS


def test_below_the_minimum_pays_nothing_and_says_too_few_views(db, pg):
    job_id = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)

    result = billing.add_curve(
        db,
        WEB_USER_ID,
        job_id,
        **curve_args(job_id, facts_=facts(now(), views=MIN_VIEWS - 1)),
    )

    assert result["rewarded_cents"] == 0
    assert result["reason"] == "too_few_views"
    assert "reward_from" not in result
    [snapshot] = pg.rows("SELECT * FROM retention_snapshots WHERE job_id = %s::uuid", job_id)
    assert snapshot["checked_views"] == MIN_VIEWS - 1
    assert snapshot["rewarded_cents"] == 0
    assert pg.job(job_id)["curve_rewarded_at"] is None
    assert "curve_reward" not in pg.kinds(WEB_USER_ID)


def test_a_hidden_view_count_pays_at_once(db, pg):
    job_id = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)

    result = billing.add_curve(
        db,
        WEB_USER_ID,
        job_id,
        **curve_args(job_id, facts_=facts(now(), views=None)),
    )

    assert result["rewarded_cents"] == PRICE_CENTS
    assert result["reason"] is None
    assert pg.job(job_id)["curve_rewarded_at"] is not None
    [snapshot] = snapshot_rows(pg, job_id)
    assert snapshot["checked_views"] is None


def test_unknown_facts_pay_as_before_and_store_the_questions(db, pg):
    job_id = make_job(db)
    pg.age(job_id, created_s=8 * DAY_S)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)

    result = billing.add_curve(
        db,
        WEB_USER_ID,
        job_id,
        **curve_args(job_id, facts_=None, views=VIEWS, published=PUBLISHED),
    )

    assert result["rewarded_cents"] == PRICE_CENTS
    assert result["reason"] is None
    [snapshot] = snapshot_rows(pg, job_id)
    assert snapshot["checked_views"] is None
    assert snapshot["views"] == VIEWS
    assert snapshot["published"] == PUBLISHED


def test_few_views_with_a_length_mismatch_is_a_length_mismatch(db, pg):
    job_id = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)

    result = billing.add_curve(
        db,
        WEB_USER_ID,
        job_id,
        **curve_args(job_id, facts_=facts(now(), duration_s=33, views=640)),
    )

    assert result["rewarded_cents"] == 0
    assert result["reason"] == "length_mismatch"


def test_the_check_and_the_save_answer_with_only_cents_and_reason(db, pg):
    job_id = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    facts_ = facts(now(), views=MIN_VIEWS)
    video_ref = video_ref_for(job_id)

    result = billing.curve_offer(
        db,
        WEB_USER_ID,
        job_id,
        video_ref,
        facts_,
        **REWARD,
        length_tolerance_s=LENGTH_TOLERANCE_S,
        min_views=MIN_VIEWS,
    )
    saved = billing.add_curve(db, WEB_USER_ID, job_id, **curve_args(job_id, facts_=facts_))

    assert len(result) == 2
    cents, reason = result
    assert (cents, reason) == (PRICE_CENTS, None)
    assert saved["reason"] == reason
    assert "reward_from" not in saved


@pytest.mark.parametrize(
    "case",
    [
        "pays_now",
        "exactly_the_minimum",
        "too_few_views",
        "too_few_views_on_a_young_video",
        "hidden_views",
        "unknown_facts",
        "few_views_with_a_length_mismatch",
    ],
)
def test_the_check_and_the_save_give_the_same_reason(db, pg, case):
    age_s, facts_, sha = reward_case(case)
    job_id = make_job(db)
    pg.age(job_id, created_s=age_s)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    args = {
        "views": VIEWS if facts_ is None else None,
        "published": PUBLISHED if facts_ is None else None,
    }

    cents, reason = offer(db, WEB_USER_ID, job_id, video_ref=YOUTUBE_REF, facts_=facts_)
    saved = billing.add_curve(
        db, WEB_USER_ID, job_id, **curve_args(job_id, sha=sha, facts_=facts_, **args)
    )

    assert saved["reason"] == reason
    if reason is None:
        assert saved["rewarded_cents"] == cents == PRICE_CENTS
    if reason == "too_few_views":
        assert saved["rewarded_cents"] == 0
        assert cents == PRICE_CENTS


def test_the_save_route_stores_a_low_view_curve_without_paying(
    db, aws, make_cfg, tmp_path, monkeypatch, pg
):
    client = app_client(aws, db, make_cfg, tmp_path)
    job_id = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    patch_lookup(monkeypatch, facts(days_ago(30), views=640))
    body = {
        "report": report_text(curve()),
        "video_url": YOUTUBE_LINK,
        "same_cut": "same",
        "promoted": False,
    }

    resp = client.post(f"/api/jobs/{job_id}/retention", json=body)

    assert resp.status_code == 200
    [snapshot] = snapshot_rows(pg, job_id)
    assert snapshot["checked_views"] == 640
    assert snapshot["rewarded_cents"] == 0
    assert pg.job(job_id)["curve_rewarded_at"] is None


def test_the_check_reports_the_checked_views_and_the_configured_minimum(
    db, aws, make_cfg, tmp_path, monkeypatch
):
    client = app_client(aws, db, make_cfg, tmp_path, curve_min_views=1500)
    job_id = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    patch_lookup(monkeypatch, facts(days_ago(30), views=1200))

    resp = client.post(f"/api/jobs/{job_id}/retention/check", json={"video_url": YOUTUBE_LINK})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["reason"] == "too_few_views"
    assert data["checked_views"] == 1200
    assert data["min_views"] == 1500
    assert data["reward_cents"] == PRICE_CENTS
    assert "reward_from" not in data


def test_the_check_reports_hidden_views_as_null(db, aws, make_cfg, tmp_path, monkeypatch):
    client = app_client(aws, db, make_cfg, tmp_path)
    job_id = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    patch_lookup(monkeypatch, facts(days_ago(30), views=None))

    resp = client.post(f"/api/jobs/{job_id}/retention/check", json={"video_url": YOUTUBE_LINK})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["reason"] is None
    assert data["checked_views"] is None
    assert data["min_views"] == MIN_VIEWS


def test_the_history_preview_still_ignores_the_view_count(db, pg):
    job_id = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    billing.add_curve(
        db,
        WEB_USER_ID,
        job_id,
        **curve_args(job_id, facts_=facts(days_ago(30), views=640)),
    )

    [row] = billing.list_jobs(db, WEB_USER_ID, 50, **REWARD)

    assert row["curve_reward_cents"] == PRICE_CENTS
    assert row["curve_rewarded"] is False


def test_the_week_setting_is_gone():
    from neurolens import settings

    research = settings.load_settings().get("research", {})

    assert "curve_reward_min_days" not in research
    assert not hasattr(billing, "DEFAULT_REWARD_MIN_DAYS")
