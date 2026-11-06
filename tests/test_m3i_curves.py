"""M3i curve collection (docs/M3i_spec.md §3-§4, §6, §11).

`results.parse_video_link` accepts the YouTube and Facebook forms and names each refusal.
`results.parse_facebook_retention` accepts only Meta's documented retention reply. `add_curve`
links a curve to its published video, pays at once once the views rule is met, and refuses a
link taken by another account or a job already linked to another video. `list_jobs` previews the
reward amount from the same rule, without a promised date.

Money and snapshots are read directly from PostgreSQL (`pg`); time moves by changing the job's
`created_at`, never by sleeping.
"""

import json
import uuid

import pytest
from conftest import MIGRATIONS, WEB_USER_ID
from neurolens import billing, pricing, results, storage

STARTER_CENTS = 10_000
PRICE_CENTS = pricing.estimate_cost_cents(27.4)
OTHER_USER = "other-user"
VIEWS = "lt_1k"
PUBLISHED = "week"
DAY_S = 24 * 60 * 60

YOUTUBE_ID = "dQw4w9WgXcQ"
YOUTUBE_REF = f"youtube:{YOUTUBE_ID}"
FACEBOOK_ID = "1234567890"
FACEBOOK_REF = f"facebook:{FACEBOOK_ID}"

LINK_MESSAGE = "Paste the video's YouTube or Facebook link."
REELS_MESSAGE = "Facebook Reels aren't supported yet."
SHORT_LINK_MESSAGE = "Open the link and paste the full facebook.com address from the browser bar."
FACEBOOK_SHAPE_MESSAGE = (
    "This Facebook report isn't in the expected shape. Please send it to the NeuroLens team."
)
FACEBOOK_ID_MESSAGE = "This report is for a different video than the link."


# ---------------------------------------------------------------- fixtures and helpers


def curve(points=20, *, first=1.0, last=0.5):
    """A descending curve whose watch ratio spread is first - last."""
    return [(i / (points - 1), first + (last - first) * i / (points - 1)) for i in range(points)]


def video_ref_for(job_id):
    return f"youtube:{job_id.replace('-', '')[:11]}"


def curve_args(job_id, *, sha="a" * 64, points=None, flags=None, video_ref=None, **overrides):
    snapshot_id = str(uuid.uuid4())
    args = {
        "snapshot_id": snapshot_id,
        "raw_key": f"dataset/{job_id}/retention/{snapshot_id}.json",
        "csv_key": f"dataset/{job_id}/retention/{snapshot_id}.csv",
        "curve_sha256": sha,
        "points": points if points is not None else curve(),
        "quality_flags": flags if flags is not None else [],
        "views": VIEWS,
        "published": PUBLISHED,
        "promoted": False,
        "reward_cap": 5,
        "reward_window_days": 30,
        "video_ref": video_ref if video_ref is not None else video_ref_for(job_id),
        "same_cut": "same",
        "source": "youtube_api",
        "facts": None,
        "length_tolerance_s": 2,
    }
    args.update(overrides)
    return args


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


def stop_sharing_and_purge(aws, db, job_id):
    """The Stop-sharing route's two steps (M3f §5c)."""
    assert billing.mark_dataset_removed(db, job_id, "stopped", user_id=WEB_USER_ID) is True
    billing.purge_job_files(db, aws.s3, aws.bucket, job_id)


def delete_and_purge(aws, db, job_id):
    """The job-delete route's two steps (M3f §5c)."""
    assert billing.delete_job(db, WEB_USER_ID, job_id) is True
    billing.purge_job_files(db, aws.s3, aws.bucket, job_id)


def migration_013_update():
    """The backfill UPDATE from migration 013, split as conftest splits migrations (M3a §3c)."""
    statements = []
    current = []
    for line in (MIGRATIONS / "013_curve_reward_ref.sql").read_text().splitlines():
        current.append(line)
        if line.rstrip().endswith(";"):
            statements.append("\n".join(current).strip())
            current = []
    [update] = [statement for statement in statements if "UPDATE JOBS" in statement.upper()]
    return update


def facebook_values():
    return {str(i): 1.0 - 0.5 * i / 39 for i in range(40)}


def facebook_report(*, video_id=FACEBOOK_ID, values=None, name=None, period=None, data=None):
    item = {
        "name": name if name is not None else "total_video_retention_graph",
        "period": period if period is not None else "lifetime",
        "id": f"{video_id}/video_insights",
        "values": [{"value": facebook_values() if values is None else values}],
    }
    return json.dumps({"data": [item] if data is None else data})


# ---------------------------------------------------------------- parse_video_link


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={YOUTUBE_ID}",
        f"youtube.com/watch?v={YOUTUBE_ID}",
        f"m.youtube.com/watch?v={YOUTUBE_ID}",
        f"https://youtu.be/{YOUTUBE_ID}",
        f"youtu.be/{YOUTUBE_ID}",
        f"https://www.youtube.com/shorts/{YOUTUBE_ID}",
        f"https://www.youtube.com/live/{YOUTUBE_ID}",
        f"https://www.youtube.com/embed/{YOUTUBE_ID}",
        f"https://www.youtube.com/watch?t=42&v={YOUTUBE_ID}",
        f"  https://youtu.be/{YOUTUBE_ID}  ",
    ],
    ids=[
        "watch",
        "watch_without_scheme",
        "mobile_watch",
        "short_link",
        "short_link_without_scheme",
        "shorts",
        "live",
        "embed",
        "other_query_parameters",
        "whitespace",
    ],
)
def test_every_youtube_link_form_gives_the_platform_and_id(url):
    assert results.parse_video_link(url) == ("youtube", YOUTUBE_ID)


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.facebook.com/MyPage/videos/{FACEBOOK_ID}",
        f"facebook.com/MyPage/videos/{FACEBOOK_ID}",
        f"m.facebook.com/MyPage/videos/{FACEBOOK_ID}",
        f"https://www.facebook.com/MyPage/videos/my-video-slug/{FACEBOOK_ID}",
        f"https://www.facebook.com/watch/?v={FACEBOOK_ID}",
        f"https://www.facebook.com/watch?v={FACEBOOK_ID}",
        f"https://www.facebook.com/video.php?v={FACEBOOK_ID}",
    ],
    ids=[
        "page_video",
        "page_video_without_scheme",
        "mobile_page_video",
        "page_video_with_slug",
        "watch_with_slash",
        "watch",
        "video_php",
    ],
)
def test_every_facebook_link_form_gives_the_platform_and_id(url):
    assert results.parse_video_link(url) == ("facebook", FACEBOOK_ID)


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.facebook.com/reel/{FACEBOOK_ID}",
        "https://www.facebook.com/share/r/AbCdEf",
    ],
    ids=["reel", "share_reel"],
)
def test_facebook_reels_are_refused_with_their_own_message(url):
    with pytest.raises(ValueError) as exc:
        results.parse_video_link(url)

    assert str(exc.value) == REELS_MESSAGE


@pytest.mark.parametrize(
    "url",
    [
        "https://fb.watch/AbCdEf/",
        "https://www.facebook.com/share/v/AbCdEf/",
    ],
    ids=["fb_watch", "share_video"],
)
def test_facebook_short_links_ask_for_the_full_address(url):
    with pytest.raises(ValueError) as exc:
        results.parse_video_link(url)

    assert str(exc.value) == SHORT_LINK_MESSAGE


@pytest.mark.parametrize(
    "url",
    [
        "https://vimeo.com/123456789",
        "https://example.com/watch?v=dQw4w9WgXcQ",
        "",
        "https://www.youtube.com/watch?v=dQw4w9WgX",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQx",
        "https://www.youtube.com/watch?v=dQw4w9WgXc!",
        f"https://www.youtube.com/watch?v={YOUTUBE_ID}&" + "x" * 300,
    ],
    ids=["other_site", "other_host", "empty", "ten_char_id", "twelve_char_id", "bad_id", "long"],
)
def test_anything_else_is_refused_with_the_generic_message(url):
    with pytest.raises(ValueError) as exc:
        results.parse_video_link(url)

    assert str(exc.value) == LINK_MESSAGE


# ---------------------------------------------------------------- parse_facebook_retention


def test_a_valid_facebook_reply_returns_forty_pairs_from_zero_to_0975():
    values = facebook_values()

    pairs = results.parse_facebook_retention(facebook_report(), FACEBOOK_ID)

    assert len(pairs) == 40
    assert [elapsed for elapsed, _ in pairs] == [i / 40 for i in range(40)]
    assert [watch for _, watch in pairs] == pytest.approx([values[str(i)] for i in range(40)])
    assert pairs[0][0] == 0
    assert pairs[-1][0] == pytest.approx(0.975)


def test_a_facebook_reply_for_another_video_is_refused():
    with pytest.raises(ValueError) as exc:
        results.parse_facebook_retention(facebook_report(video_id="9999999999"), FACEBOOK_ID)

    assert str(exc.value) == FACEBOOK_ID_MESSAGE


def _shape_cases():
    values = facebook_values()
    thirty_nine = {key: value for key, value in values.items() if key != "39"}
    forty_one = {**values, "40": 0.4}
    missing_twenty = {key: value for key, value in values.items() if key != "20"}
    above_one = {**values, "39": 1.01}
    not_a_number = {**values, "39": "0.5"}
    item = {
        "name": "total_video_retention_graph",
        "period": "lifetime",
        "id": f"{FACEBOOK_ID}/video_insights",
        "values": [{"value": values}],
    }
    return [
        ("thirty_nine_keys", facebook_report(values=thirty_nine)),
        ("forty_one_keys", facebook_report(values=forty_one)),
        ("missing_key", facebook_report(values=missing_twenty)),
        ("value_above_one", facebook_report(values=above_one)),
        ("value_not_a_number", facebook_report(values=not_a_number)),
        ("two_metrics", facebook_report(data=[item, item])),
        ("no_data", json.dumps({"meta": {}})),
        ("wrong_name", facebook_report(name="other_metric")),
        ("wrong_period", facebook_report(period="day")),
        ("empty_values", facebook_report(values={})),
        ("value_not_an_object", json.dumps({"data": [{**item, "values": [{"value": [1.0]}]}]})),
    ]


@pytest.mark.parametrize(("case", "text"), _shape_cases(), ids=[case for case, _ in _shape_cases()])
def test_a_facebook_reply_outside_the_documented_shape_is_refused(case, text):
    with pytest.raises(ValueError) as exc:
        results.parse_facebook_retention(text, FACEBOOK_ID)

    assert str(exc.value) == FACEBOOK_SHAPE_MESSAGE, case


# ---------------------------------------------------------------- add_curve: the one rule


def test_a_valid_curve_pays_at_once_regardless_of_age(db, pg):
    job_id, _ = make_job(db)
    pg.age(job_id, created_s=6 * DAY_S)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)

    result = billing.add_curve(db, WEB_USER_ID, job_id, **curve_args(job_id))

    assert result["rewarded_cents"] == PRICE_CENTS
    assert result["quality"] == []
    assert result["reason"] is None
    [snapshot] = snapshot_rows(pg, job_id)
    assert snapshot["rewarded_cents"] == PRICE_CENTS
    assert snapshot["video_ref"] == video_ref_for(job_id)
    assert snapshot["same_cut"] == "same"
    assert snapshot["source"] == "youtube_api"
    assert pg.job(job_id)["curve_rewarded_at"] is not None
    assert pg.kinds(WEB_USER_ID).count("curve_reward") == 1


def test_a_second_valid_curve_on_the_same_job_is_not_rewarded_again(db, pg):
    job_id, _ = make_job(db)
    pg.age(job_id, created_s=6 * DAY_S)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    first = billing.add_curve(db, WEB_USER_ID, job_id, **curve_args(job_id))
    assert first["rewarded_cents"] == PRICE_CENTS

    second = billing.add_curve(
        db,
        WEB_USER_ID,
        job_id,
        **curve_args(job_id, sha="b" * 64, points=curve(last=0.6)),
    )

    assert second["rewarded_cents"] == 0
    assert second["reason"] == "already_rewarded"
    assert pg.balance(WEB_USER_ID) == (STARTER_CENTS + 100, 0)
    assert [row["rewarded_cents"] for row in snapshot_rows(pg, job_id)] == [PRICE_CENTS, 0]
    assert pg.kinds(WEB_USER_ID).count("curve_reward") == 1


# ---------------------------------------------------------------- add_curve: the link rules


def test_the_same_link_on_another_accounts_job_is_video_taken_and_stores_nothing(db, pg):
    first, _ = make_job(db)
    second, _ = make_job(db, user_id=OTHER_USER)
    for job_id in (first, second):
        pg.age(job_id, created_s=8 * DAY_S)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    billing.grant_credit(db, f"{OTHER_USER}@example.com", 100)
    billing.add_curve(db, WEB_USER_ID, first, **curve_args(first, video_ref=YOUTUBE_REF))
    before = pg.snapshot()

    with pytest.raises(billing.VideoTaken) as exc:
        billing.add_curve(
            db,
            OTHER_USER,
            second,
            **curve_args(second, sha="b" * 64, video_ref=YOUTUBE_REF),
        )

    assert str(exc.value) == "This video is linked to another account."
    assert pg.snapshot() == before
    assert snapshot_rows(pg, second) == []


def test_the_same_link_on_two_of_the_same_users_jobs_both_save_but_only_one_pays(db, pg):
    first, _ = make_job(db, sha="video-one")
    second, _ = make_job(db, sha="video-two")
    for job_id in (first, second):
        pg.age(job_id, created_s=8 * DAY_S)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)

    first_result = billing.add_curve(
        db, WEB_USER_ID, first, **curve_args(first, video_ref=YOUTUBE_REF)
    )
    second_result = billing.add_curve(
        db,
        WEB_USER_ID,
        second,
        **curve_args(second, sha="b" * 64, points=curve(last=0.6), video_ref=YOUTUBE_REF),
    )

    assert first_result["rewarded_cents"] == PRICE_CENTS
    assert second_result["rewarded_cents"] == 0
    assert len(snapshot_rows(pg, first)) == 1
    assert len(snapshot_rows(pg, second)) == 1
    assert pg.kinds(WEB_USER_ID).count("curve_reward") == 1


def test_a_second_link_on_a_job_that_already_has_one_is_video_link_changed(db, pg):
    job_id, _ = make_job(db)
    pg.age(job_id, created_s=8 * DAY_S)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    billing.add_curve(db, WEB_USER_ID, job_id, **curve_args(job_id, video_ref=FACEBOOK_REF))
    before = pg.snapshot()

    with pytest.raises(billing.VideoLinkChanged) as exc:
        billing.add_curve(
            db,
            WEB_USER_ID,
            job_id,
            **curve_args(job_id, sha="b" * 64, video_ref="youtube:abcdefghijk"),
        )

    assert str(exc.value) == "This analysis is already linked to another video."
    assert pg.snapshot() == before
    assert len(snapshot_rows(pg, job_id)) == 1


# ---------------------------------------------------------------- add_curve: reward outlives copy


def test_a_link_rewarded_before_stop_sharing_cannot_be_rewarded_again_on_a_reencoded_upload(
    aws, db, pg
):
    first, _ = make_job(db, sha="video-one")
    pg.age(first, created_s=8 * DAY_S)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    before = billing.add_curve(db, WEB_USER_ID, first, **curve_args(first, video_ref=YOUTUBE_REF))
    assert before["rewarded_cents"] == PRICE_CENTS

    stop_sharing_and_purge(aws, db, first)
    assert snapshot_rows(pg, first) == []

    second, _ = make_job(db, sha="video-two")
    pg.age(second, created_s=8 * DAY_S)
    balance = pg.balance(WEB_USER_ID)

    after = billing.add_curve(
        db,
        WEB_USER_ID,
        second,
        **curve_args(second, sha="b" * 64, points=curve(last=0.6), video_ref=YOUTUBE_REF),
    )

    assert after["rewarded_cents"] == 0
    assert after["reason"] == "already_rewarded"
    assert pg.balance(WEB_USER_ID) == balance
    offer = billing.curve_offer(
        db,
        WEB_USER_ID,
        second,
        YOUTUBE_REF,
        None,
        reward_cap=5,
        reward_window_days=30,
        length_tolerance_s=2,
    )
    assert offer[1] == "already_rewarded"


def test_a_link_rewarded_before_the_job_is_deleted_cannot_be_rewarded_again_on_a_reencoded_upload(
    aws, db, pg
):
    first, _ = make_job(db, sha="video-one")
    pg.age(first, created_s=8 * DAY_S)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    before = billing.add_curve(db, WEB_USER_ID, first, **curve_args(first, video_ref=YOUTUBE_REF))
    assert before["rewarded_cents"] == PRICE_CENTS

    delete_and_purge(aws, db, first)
    assert snapshot_rows(pg, first) == []

    second, _ = make_job(db, sha="video-two")
    pg.age(second, created_s=8 * DAY_S)
    balance = pg.balance(WEB_USER_ID)

    after = billing.add_curve(
        db,
        WEB_USER_ID,
        second,
        **curve_args(second, sha="b" * 64, points=curve(last=0.6), video_ref=YOUTUBE_REF),
    )

    assert after["rewarded_cents"] == 0
    assert after["reason"] == "already_rewarded"
    assert pg.balance(WEB_USER_ID) == balance
    offer = billing.curve_offer(
        db,
        WEB_USER_ID,
        second,
        YOUTUBE_REF,
        None,
        reward_cap=5,
        reward_window_days=30,
        length_tolerance_s=2,
    )
    assert offer[1] == "already_rewarded"


# ---------------------------------------------------------------- migration 013


def test_migration_013_backfills_curve_reward_ref_from_rewarded_snapshots(db, pg):
    update = migration_013_update()
    rewarded, _ = make_job(db, sha="video-one")
    unrewarded, _ = make_job(db, sha="video-two")
    rewarded_ref = "youtube:aaaaaaaaaaa"
    pg.conn.execute(
        "INSERT INTO retention_snapshots "
        "(snapshot_id, job_id, curve_sha256, video_ref, rewarded_cents) VALUES "
        "(%s::uuid, %s::uuid, %s, %s, %s), (%s::uuid, %s::uuid, %s, %s, 0)",
        (
            str(uuid.uuid4()),
            rewarded,
            "a" * 64,
            rewarded_ref,
            PRICE_CENTS,
            str(uuid.uuid4()),
            unrewarded,
            "b" * 64,
            "youtube:bbbbbbbbbbb",
        ),
    )
    pg.conn.execute("UPDATE jobs SET curve_reward_ref = NULL")

    pg.conn.execute(update)

    assert pg.job(rewarded)["curve_reward_ref"] == rewarded_ref
    assert pg.job(unrewarded)["curve_reward_ref"] is None


def test_the_database_refuses_two_jobs_with_the_same_rewarded_link(db, pg):
    from psycopg.errors import UniqueViolation

    first, _ = make_job(db, sha="video-one")
    second, _ = make_job(db, sha="video-two")
    pg.conn.execute(
        "UPDATE jobs SET curve_reward_ref = %s WHERE job_id = %s::uuid", (YOUTUBE_REF, first)
    )

    with pytest.raises(UniqueViolation):
        pg.conn.execute(
            "UPDATE jobs SET curve_reward_ref = %s WHERE job_id = %s::uuid", (YOUTUBE_REF, second)
        )

    pg.conn.execute(
        "UPDATE jobs SET curve_reward_ref = NULL WHERE job_id IN (%s::uuid, %s::uuid)",
        (first, second),
    )
    assert pg.job(first)["curve_reward_ref"] is None
    assert pg.job(second)["curve_reward_ref"] is None


# ---------------------------------------------------------------- list_jobs: the reward date


def test_list_jobs_previews_the_reward_without_a_date(db, pg):
    job_id, _ = make_job(db, consent=True)
    pg.age(job_id, created_s=5 * DAY_S)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)

    [row] = billing.list_jobs(db, WEB_USER_ID, 50)

    assert row["sharing"] is True
    assert row["curve_reward_cents"] == PRICE_CENTS
    assert "curve_reward_from" not in row


@pytest.mark.parametrize("consent", [False, True], ids=["unshared", "unfunded"])
def test_list_jobs_has_no_reward_date_when_no_reward_is_possible(db, pg, consent):
    job_id, _ = make_job(db, consent=consent)

    [row] = billing.list_jobs(db, WEB_USER_ID, 50)

    assert row["curve_reward_cents"] == 0
    assert "curve_reward_from" not in row
