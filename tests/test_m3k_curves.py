"""The 1,000-view curve rule (docs/M3k_spec.md §7, M3l §3, §7).

A checked video must show at least `research.curve_min_views` views before its curve pays.
Hidden and unknown view counts keep paying as before, because those creators were screened by
hand. M3l removes the one-week wait, so the view count is no longer paired with a date. The check
and the save share the rule, so their answers agree; the check's reply names the view count it
saw and the minimum it applied.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import WEB_USER_ID
from neurolens import billing, pricing, storage

STARTER_CENTS = 10_000
PRICE_CENTS = pricing.estimate_cost_cents(30.1)
VIEWS = "lt_1k"
PUBLISHED = "week"
LENGTH_TOLERANCE_S = 2
MIN_VIEWS = 1000
DAY_S = 24 * 60 * 60

YOUTUBE_ID = "dQw4w9WgXcQ"
YOUTUBE_LINK = f"https://www.youtube.com/watch?v={YOUTUBE_ID}"
YOUTUBE_REF = f"youtube:{YOUTUBE_ID}"


# ---------------------------------------------------------------- fixtures and helpers


def curve(points=20, *, first=1.0, last=0.5):
    return [(i / (points - 1), first + (last - first) * i / (points - 1)) for i in range(points)]


def facts(published_at, *, duration_s=31, views=25048):
    from neurolens.youtube import VideoFacts

    return VideoFacts(published_at=published_at, duration_s=duration_s, views=views)


def days_ago(days):
    return datetime.now(UTC) - timedelta(days=days)


def video_ref_for(job_id):
    return f"youtube:{job_id.replace('-', '')[:11]}"


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
        "reward_cap": 5,
        "reward_window_days": 30,
        "video_ref": video_ref if video_ref is not None else video_ref_for(job_id),
        "same_cut": "same",
        "source": "youtube_api",
        "facts": facts_,
        "length_tolerance_s": LENGTH_TOLERANCE_S,
        "min_views": min_views,
    }


def make_job(
    db,
    *,
    user_id=WEB_USER_ID,
    state="done",
    consent=True,
    seconds=30.1,
    sha="a" * 64,
    starter_cents=STARTER_CENTS,
):
    billing.ensure_user(db, user_id, f"{user_id}@example.com", starter_cents)
    job_id = str(uuid.uuid4())
    key = storage.object_key(user_id, job_id, "video/mp4")
    billing.reserve(
        db, user_id, job_id, key, "clip.mp4", round(seconds * 1000), research_consent=consent
    )
    if state != "queued":
        assert billing.verify_queued(db, job_id, round(seconds * 1000), 3600) == "ok"
    if state in ("processing", "done", "failed"):
        assert billing.claim(db, job_id) == 1
    if state == "done":
        assert billing.settle_success(db, job_id) is True
    elif state == "failed":
        assert billing.issue_refund(db, job_id, "processing_failed", attempt=1) is True
    if sha is not None and state != "queued":
        billing.record_video_hash(db, job_id, sha)
    return job_id, key


def snapshot_rows(pg, job_id):
    return pg.rows(
        "SELECT * FROM retention_snapshots WHERE job_id = %s::uuid ORDER BY added_at", job_id
    )


def offer(db, user_id, job_id, *, video_ref, facts_, min_views=MIN_VIEWS):
    args = {
        "reward_cap": 5,
        "reward_window_days": 30,
        "length_tolerance_s": LENGTH_TOLERANCE_S,
        "min_views": min_views,
    }
    cents, reason = billing.curve_offer(db, user_id, job_id, video_ref, facts_, **args)
    return cents, reason


def patch_lookup(monkeypatch, result):
    from neurolens import youtube

    monkeypatch.setattr(youtube, "lookup", lambda video_id, api_key, *, timeout=5: result)


def app_client(
    aws,
    db,
    make_cfg,
    tmp_path,
    *,
    curve_min_views=MIN_VIEWS,
):
    from neurolens.web.app import create_app

    research = {
        "curve_rewards_per_window": 5,
        "curve_reward_window_days": 30,
        "curve_length_tolerance_s": LENGTH_TOLERANCE_S,
        "curve_min_views": curve_min_views,
        "no_curve_days": 90,
    }
    cfg = make_cfg(research=research, youtube_api_key="test-youtube-key")
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


# ---------------------------------------------------------------- the reward and the stored facts


def test_a_checked_video_below_the_minimum_is_stored_but_pays_nothing(db, pg):
    job_id, _ = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)

    result = billing.add_curve(
        db,
        WEB_USER_ID,
        job_id,
        **curve_args(job_id, facts_=facts(days_ago(30), views=999)),
    )

    assert result["rewarded_cents"] == 0
    assert result["reason"] == "too_few_views"
    assert "reward_from" not in result
    [snapshot] = snapshot_rows(pg, job_id)
    assert snapshot["checked_views"] == 999
    assert snapshot["rewarded_cents"] == 0
    assert pg.job(job_id)["curve_rewarded_at"] is None
    assert "curve_reward" not in pg.kinds(WEB_USER_ID)


def test_a_checked_video_at_the_minimum_is_rewarded(db, pg):
    job_id, _ = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)

    result = billing.add_curve(
        db,
        WEB_USER_ID,
        job_id,
        **curve_args(job_id, facts_=facts(days_ago(30), views=MIN_VIEWS)),
    )

    assert result["rewarded_cents"] == PRICE_CENTS
    assert result["reason"] is None
    assert snapshot_rows(pg, job_id)[0]["checked_views"] == MIN_VIEWS
    assert pg.job(job_id)["curve_rewarded_at"] is not None


def test_hidden_views_are_rewarded_as_before(db, pg):
    job_id, _ = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)

    result = billing.add_curve(
        db,
        WEB_USER_ID,
        job_id,
        **curve_args(job_id, facts_=facts(days_ago(30), views=None)),
    )

    assert result["rewarded_cents"] == PRICE_CENTS
    assert result["reason"] is None
    assert snapshot_rows(pg, job_id)[0]["checked_views"] is None


def test_unknown_facts_are_rewarded_as_before(db, pg):
    job_id, _ = make_job(db)
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


def test_few_views_names_the_views_without_a_date(db, pg):
    job_id, _ = make_job(db)
    pg.age(job_id, created_s=10 * DAY_S)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)

    result = billing.add_curve(
        db,
        WEB_USER_ID,
        job_id,
        **curve_args(job_id, facts_=facts(days_ago(2), views=640)),
    )

    assert result["rewarded_cents"] == 0
    assert result["reason"] == "too_few_views"
    assert "reward_from" not in result
    assert snapshot_rows(pg, job_id)[0]["checked_views"] == 640


def test_few_views_with_a_length_mismatch_is_a_length_mismatch(db, pg):
    job_id, _ = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)

    result = billing.add_curve(
        db,
        WEB_USER_ID,
        job_id,
        **curve_args(job_id, facts_=facts(days_ago(30), duration_s=33, views=640)),
    )

    assert result["rewarded_cents"] == 0
    assert result["reason"] == "length_mismatch"


# ---------------------------------------------------------------- the check agrees with the save


def reward_case(name):
    now = datetime.now(UTC)
    cases = {
        "pays_now": (0, facts(now - timedelta(days=30), views=25048), "a" * 64),
        "exactly_the_minimum": (0, facts(now - timedelta(days=30), views=MIN_VIEWS), "b" * 64),
        "too_few_views": (0, facts(now - timedelta(days=30), views=999), "c" * 64),
        "too_few_views_on_a_young_video": (
            10 * DAY_S,
            facts(now - timedelta(days=2), views=640),
            "d" * 64,
        ),
        "hidden_views": (0, facts(now - timedelta(days=30), views=None), "e" * 64),
        "unknown_facts": (0, None, "f" * 64),
        "few_views_with_a_length_mismatch": (
            0,
            facts(now - timedelta(days=30), duration_s=33, views=640),
            "1" * 64,
        ),
    }
    return cases[name]


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
    job_id, _ = make_job(db)
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


# ---------------------------------------------------------------- the save route


def test_the_save_route_stores_a_low_view_curve_without_paying(
    db, aws, make_cfg, tmp_path, monkeypatch, pg
):
    client = app_client(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
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


# ---------------------------------------------------------------- the check's reply


def test_the_check_reports_the_checked_views_and_the_configured_minimum(
    db, aws, make_cfg, tmp_path, monkeypatch
):
    client = app_client(aws, db, make_cfg, tmp_path, curve_min_views=1500)
    job_id, _ = make_job(db)
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
    job_id, _ = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    patch_lookup(monkeypatch, facts(days_ago(30), views=None))

    resp = client.post(f"/api/jobs/{job_id}/retention/check", json={"video_url": YOUTUBE_LINK})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["reason"] is None
    assert data["checked_views"] is None
    assert data["min_views"] == MIN_VIEWS


# ---------------------------------------------------------------- the preview is unchanged


def test_the_history_preview_still_ignores_the_view_count(db, pg):
    job_id, _ = make_job(db, consent=True)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    billing.add_curve(
        db,
        WEB_USER_ID,
        job_id,
        **curve_args(job_id, facts_=facts(days_ago(30), views=640)),
    )

    [row] = billing.list_jobs(db, WEB_USER_ID, 50)

    assert row["curve_reward_cents"] == PRICE_CENTS
    assert row["curve_rewarded"] is False
