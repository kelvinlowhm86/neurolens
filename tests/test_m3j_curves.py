"""M3j checked publish dates and length (docs/M3j_spec.md §3, §5, §11).

`youtube.lookup` turns the Data API's reply into facts or refuses loudly without leaking the key.
The reward now depends on the video's publish date: a video public for a week pays at once, a
young one waits, and a video of a different length is saved without credit. The check and the
save use the same rules, so their answers agree. Money and snapshots are read directly from
PostgreSQL; time moves by changing the job's `created_at` or the facts' `published_at`, never by
sleeping.
"""

import importlib
import json
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import WEB_USER_ID
from neurolens import billing, pricing, storage

STARTER_CENTS = 10_000
PRICE_CENTS = pricing.estimate_cost_cents(30.1)
VIEWS = "lt_1k"
PUBLISHED = "week"
REWARD_MIN_DAYS = 7
LENGTH_TOLERANCE_S = 2
DAY_S = 24 * 60 * 60

YOUTUBE_ID = "dQw4w9WgXcQ"
YOUTUBE_REF = f"youtube:{YOUTUBE_ID}"
MEASURED_PUBLISHED = datetime(2024, 2, 12, 17, 28, 22, tzinfo=UTC)


# ---------------------------------------------------------------- YouTube's HTTP boundary


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.status = status

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(status):
    return urllib.error.HTTPError(
        "https://www.googleapis.com/youtube/v3/videos?key=the-secret-key",
        status,
        "error",
        {},
        None,
    )


def patch_urlopen(monkeypatch, responder):
    """Fake YouTube's HTTP boundary, whichever way neurolens.youtube imports urlopen."""

    def fake_urlopen(request, *args, **kwargs):
        return responder(request)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    module = importlib.import_module("neurolens.youtube")
    if hasattr(module, "urlopen"):
        monkeypatch.setattr(module, "urlopen", fake_urlopen)


def video_reply(*, published="2024-02-12T17:28:22Z", duration="PT31S", views="25048"):
    statistics = {} if views is None else {"viewCount": views}
    return {
        "items": [
            {
                "snippet": {"publishedAt": published},
                "contentDetails": {"duration": duration},
                "statistics": statistics,
            }
        ]
    }


def lookup_with(monkeypatch, reply, *, key="test-key"):
    patch_urlopen(monkeypatch, lambda request: FakeResponse(reply))
    from neurolens import youtube

    return youtube.lookup(YOUTUBE_ID, key)


def test_the_measured_reply_becomes_the_video_facts(monkeypatch):
    facts = lookup_with(monkeypatch, video_reply())

    assert facts.published_at == MEASURED_PUBLISHED
    assert facts.duration_s == 31
    assert facts.views == 25048


@pytest.mark.parametrize(
    ("duration", "seconds"),
    [("PT1M5S", 65), ("PT1H2M3S", 3723), ("P0D", 0)],
    ids=["minutes", "hours", "zero"],
)
def test_each_iso_duration_becomes_whole_seconds(monkeypatch, duration, seconds):
    facts = lookup_with(monkeypatch, video_reply(duration=duration))

    assert facts.duration_s == seconds


def test_a_reply_with_no_item_is_not_a_video(monkeypatch):
    patch_urlopen(monkeypatch, lambda request: FakeResponse({"items": []}))
    from neurolens import youtube

    assert youtube.lookup(YOUTUBE_ID, "test-key") is None


def test_hidden_views_are_unknown(monkeypatch):
    facts = lookup_with(monkeypatch, video_reply(views=None))

    assert facts.views is None


@pytest.mark.parametrize("status", [403, 500], ids=["forbidden", "server_error"])
def test_a_non_200_reply_is_a_failed_lookup(monkeypatch, status):
    patch_urlopen(monkeypatch, lambda request: (_ for _ in ()).throw(http_error(status)))
    from neurolens import youtube

    with pytest.raises(youtube.LookupFailed):
        youtube.lookup(YOUTUBE_ID, "test-key")


@pytest.mark.parametrize(
    "fail",
    [
        lambda request: (_ for _ in ()).throw(TimeoutError("timed out")),
        lambda request: FakeResponse(b"not json"),
        lambda request: FakeResponse({"items": [{"snippet": {}}]}),
    ],
    ids=["timeout", "bad_json", "missing_fields"],
)
def test_a_broken_reply_is_a_failed_lookup(monkeypatch, fail):
    patch_urlopen(monkeypatch, fail)
    from neurolens import youtube

    with pytest.raises(youtube.LookupFailed):
        youtube.lookup(YOUTUBE_ID, "test-key")


def test_a_failed_lookup_message_never_contains_the_key(monkeypatch):
    key = "the-secret-key"
    patch_urlopen(monkeypatch, lambda request: (_ for _ in ()).throw(http_error(403)))
    from neurolens import youtube

    with pytest.raises(youtube.LookupFailed) as exc:
        youtube.lookup(YOUTUBE_ID, key)

    assert key not in str(exc.value)


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
    flags=None,
    video_ref=None,
    facts_=None,
    views=None,
    published=None,
    **overrides,
):
    snapshot_id = str(uuid.uuid4())
    args = {
        "snapshot_id": snapshot_id,
        "raw_key": f"dataset/{job_id}/retention/{snapshot_id}.json",
        "csv_key": f"dataset/{job_id}/retention/{snapshot_id}.csv",
        "curve_sha256": sha,
        "points": points if points is not None else curve(),
        "quality_flags": flags if flags is not None else [],
        "views": views,
        "published": published,
        "promoted": False,
        "reward_cap": 5,
        "reward_window_days": 30,
        "video_ref": video_ref if video_ref is not None else video_ref_for(job_id),
        "same_cut": "same",
        "source": "youtube_api",
        "reward_min_days": REWARD_MIN_DAYS,
        "facts": facts_,
        "length_tolerance_s": LENGTH_TOLERANCE_S,
    }
    args.update(overrides)
    return args


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
        # The dispatcher measures the length before any GPU runs (M3e §3a).
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


def reward_date(pg, job_id, days=REWARD_MIN_DAYS):
    return storage.utc_text(pg.job(job_id)["created_at"] + timedelta(days=days))


def offer(db, user_id, job_id, *, video_ref, facts_, **overrides):
    """The check's answer as (cents, reward_from, reason), whatever container it returns."""
    args = {
        "reward_cap": 5,
        "reward_window_days": 30,
        "reward_min_days": REWARD_MIN_DAYS,
        "length_tolerance_s": LENGTH_TOLERANCE_S,
    }
    args.update(overrides)
    result = billing.curve_offer(db, user_id, job_id, video_ref, facts_, **args)
    if isinstance(result, dict):
        return result["reward_cents"], result.get("reward_from"), result["reason"]
    cents, reward_from, reason = result
    return cents, reward_from, reason


# ---------------------------------------------------------------- the reward and the written facts


def test_a_video_public_for_a_month_pays_at_once_and_stores_the_checked_facts(db, pg):
    job_id, _ = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    published = days_ago(30)

    result = billing.add_curve(
        db, WEB_USER_ID, job_id, **curve_args(job_id, facts_=facts(published))
    )

    assert result["rewarded_cents"] == PRICE_CENTS
    assert result["reason"] is None
    [snapshot] = snapshot_rows(pg, job_id)
    assert snapshot["checked_views"] == 25048
    assert snapshot["video_seconds"] == 31
    assert abs((snapshot["published_at"] - published).total_seconds()) < 1
    assert snapshot["views"] is None
    assert snapshot["published"] is None
    assert pg.job(job_id)["curve_rewarded_at"] is not None


def test_a_young_video_waits_until_a_week_after_it_was_published(db, pg):
    job_id, _ = make_job(db)
    pg.age(job_id, created_s=10 * DAY_S)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    published = days_ago(2)
    expected_from = storage.utc_text(published + timedelta(days=REWARD_MIN_DAYS))

    result = billing.add_curve(
        db, WEB_USER_ID, job_id, **curve_args(job_id, facts_=facts(published))
    )

    assert result["rewarded_cents"] == 0
    assert result["reason"] == "too_early"
    assert result["reward_from"] == expected_from
    assert pg.job(job_id)["curve_rewarded_at"] is None
    assert "curve_reward" not in pg.kinds(WEB_USER_ID)


def test_unknown_facts_still_wait_a_week_after_the_analysis(db, pg):
    job_id, _ = make_job(db)
    pg.age(job_id, created_s=6 * DAY_S)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)

    result = billing.add_curve(
        db,
        WEB_USER_ID,
        job_id,
        **curve_args(job_id, facts_=None, views=VIEWS, published=PUBLISHED),
    )

    assert result["rewarded_cents"] == 0
    assert result["reason"] == "too_early"
    assert result["reward_from"] == reward_date(pg, job_id)


@pytest.mark.parametrize(
    ("youtube_seconds", "paid"),
    [(33, False), (31, True)],
    ids=["three_seconds_of_video_out", "within_two_seconds"],
)
def test_the_length_must_match_the_analysed_video_within_two_seconds(db, pg, youtube_seconds, paid):
    job_id, _ = make_job(db, seconds=30.1)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    published = days_ago(30)

    result = billing.add_curve(
        db,
        WEB_USER_ID,
        job_id,
        **curve_args(job_id, facts_=facts(published, duration_s=youtube_seconds)),
    )

    [snapshot] = snapshot_rows(pg, job_id)
    assert snapshot["video_seconds"] == youtube_seconds
    if paid:
        assert result["rewarded_cents"] == PRICE_CENTS
        assert result["reason"] is None
    else:
        assert result["rewarded_cents"] == 0
        assert result["reason"] == "length_mismatch"
        assert pg.job(job_id)["curve_rewarded_at"] is None
        assert "curve_reward" not in pg.kinds(WEB_USER_ID)


def test_unknown_facts_skip_the_length_check(db, pg):
    job_id, _ = make_job(db, seconds=30.1)
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


# ---------------------------------------------------------------- the check agrees with the save


def reward_case(name):
    """A job, its facts and the reason both the check and the save must give."""
    now = datetime.now(UTC)
    cases = {
        "pays_now": (0, facts(now - timedelta(days=30)), "a" * 64),
        "too_early_from_publish": (
            10 * DAY_S,
            facts(now - timedelta(days=2)),
            "b" * 64,
        ),
        "too_early_from_analysis": (6 * DAY_S, None, "c" * 64),
        "pays_from_analysis": (8 * DAY_S, None, "d" * 64),
        "length_mismatch": (
            0,
            facts(now - timedelta(days=30), duration_s=33),
            "e" * 64,
        ),
        "length_within_tolerance": (
            0,
            facts(now - timedelta(days=30), duration_s=31),
            "f" * 64,
        ),
        "not_funded": (0, facts(now - timedelta(days=30)), "1" * 64),
    }
    return cases[name]


@pytest.mark.parametrize(
    "case",
    [
        "pays_now",
        "too_early_from_publish",
        "too_early_from_analysis",
        "pays_from_analysis",
        "length_mismatch",
        "length_within_tolerance",
        "not_funded",
    ],
)
def test_the_check_and_the_save_give_the_same_reason(db, pg, case):
    age_s, facts_, sha = reward_case(case)
    job_id, _ = make_job(db)
    pg.age(job_id, created_s=age_s)
    if case != "not_funded":
        billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    args = {
        "views": VIEWS if facts_ is None else None,
        "published": PUBLISHED if facts_ is None else None,
    }

    cents, _, reason = offer(db, WEB_USER_ID, job_id, video_ref=YOUTUBE_REF, facts_=facts_)
    saved = billing.add_curve(
        db, WEB_USER_ID, job_id, **curve_args(job_id, sha=sha, facts_=facts_, **args)
    )

    assert saved["reason"] == reason
    if reason is None:
        assert saved["rewarded_cents"] == cents == PRICE_CENTS


def test_the_check_writes_nothing(db, pg):
    job_id, _ = make_job(db)
    pg.age(job_id, created_s=8 * DAY_S)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    before = pg.snapshot()

    cents, reward_from, reason = offer(
        db,
        WEB_USER_ID,
        job_id,
        video_ref=YOUTUBE_REF,
        facts_=facts(days_ago(30)),
    )

    assert (cents, reward_from, reason) == (PRICE_CENTS, None, None)
    assert snapshot_rows(pg, job_id) == []
    assert pg.snapshot() == before


def test_the_check_refuses_a_link_taken_by_another_account(db, pg):
    other, _ = make_job(db, user_id="other-user")
    pg.age(other, created_s=8 * DAY_S)
    billing.grant_credit(db, "other-user@example.com", 100)
    billing.add_curve(
        db,
        "other-user",
        other,
        **curve_args(
            other,
            facts_=facts(days_ago(30)),
            video_ref=YOUTUBE_REF,
        ),
    )
    job_id, _ = make_job(db)

    with pytest.raises(billing.VideoTaken):
        offer(db, WEB_USER_ID, job_id, video_ref=YOUTUBE_REF, facts_=facts(days_ago(30)))

    assert snapshot_rows(pg, job_id) == []


def test_the_check_refuses_a_job_already_linked_to_another_video(db, pg):
    job_id, _ = make_job(db)
    billing.add_curve(
        db,
        WEB_USER_ID,
        job_id,
        **curve_args(job_id, facts_=facts(days_ago(30)), video_ref="facebook:1234567890"),
    )

    with pytest.raises(billing.VideoLinkChanged):
        offer(db, WEB_USER_ID, job_id, video_ref=YOUTUBE_REF, facts_=facts(days_ago(30)))


# ---------------------------------------------------------------- list_jobs has no promised date


def test_list_jobs_no_longer_promises_a_reward_date(db, pg):
    job_id, _ = make_job(db, consent=True)
    pg.age(job_id, created_s=5 * DAY_S)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)

    [row] = billing.list_jobs(db, WEB_USER_ID, 50, reward_min_days=REWARD_MIN_DAYS)

    assert row["curve_reward_cents"] == PRICE_CENTS
    assert "curve_reward_from" not in row
