"""M3l's one curve rule (docs/M3l_spec.md §3, §7).

A curve pays when every other rule passes and the checked video has at least 1,000 views. There
is no one-week wait any more: a young video with enough views pays at once, and a hidden or
unknown count skips the views check as before. No answer carries a reward date, and the old
`curve_reward_min_days` setting is gone. Money and snapshots are read directly from PostgreSQL.
"""

import uuid
from datetime import UTC, datetime

from conftest import WEB_USER_ID
from neurolens import billing, pricing, storage

STARTER_CENTS = 10_000
PRICE_CENTS = pricing.estimate_cost_cents(30.1)
LENGTH_TOLERANCE_S = 2
MIN_VIEWS = 1000
VIEWS = "lt_1k"
PUBLISHED = "week"


def curve(points=20, *, first=1.0, last=0.5):
    return [(i / (points - 1), first + (last - first) * i / (points - 1)) for i in range(points)]


def facts(published_at, *, duration_s=31, views=25048):
    from neurolens.youtube import VideoFacts

    return VideoFacts(published_at=published_at, duration_s=duration_s, views=views)


def now():
    return datetime.now(UTC)


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


def curve_args(job_id, *, sha="a" * 64, facts_=None):
    snapshot_id = str(uuid.uuid4())
    return {
        "snapshot_id": snapshot_id,
        "raw_key": f"dataset/{job_id}/retention/{snapshot_id}.json",
        "csv_key": f"dataset/{job_id}/retention/{snapshot_id}.csv",
        "curve_sha256": sha,
        "points": curve(),
        "quality_flags": [],
        "views": None if facts_ is not None else VIEWS,
        "published": None if facts_ is not None else PUBLISHED,
        "promoted": False,
        "reward_cap": 5,
        "reward_window_days": 30,
        "video_ref": f"youtube:{job_id.replace('-', '')[:11]}",
        "same_cut": "same",
        "source": "youtube_api",
        "facts": facts_,
        "length_tolerance_s": LENGTH_TOLERANCE_S,
    }


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
    [snapshot] = pg.rows(
        "SELECT * FROM retention_snapshots WHERE job_id = %s::uuid", job_id
    )
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


def test_the_check_and_the_save_answer_with_only_cents_and_reason(db, pg):
    job_id = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    facts_ = facts(now(), views=MIN_VIEWS)
    video_ref = f"youtube:{job_id.replace('-', '')[:11]}"

    offer = billing.curve_offer(
        db,
        WEB_USER_ID,
        job_id,
        video_ref,
        facts_,
        reward_cap=5,
        reward_window_days=30,
        length_tolerance_s=LENGTH_TOLERANCE_S,
    )
    saved = billing.add_curve(db, WEB_USER_ID, job_id, **curve_args(job_id, facts_=facts_))

    assert len(offer) == 2
    cents, reason = offer
    assert (cents, reason) == (PRICE_CENTS, None)
    assert saved["reason"] == reason
    assert "reward_from" not in saved


def test_the_week_setting_is_gone():
    from neurolens import settings

    research = settings.load_settings().get("research", {})

    assert "curve_reward_min_days" not in research
    assert not hasattr(billing, "DEFAULT_REWARD_MIN_DAYS")
