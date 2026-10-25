"""M3f research curves (docs/M3f_spec.md §5d, §10 "add_curve", "curve_quality",
"Retention route").

`billing.add_curve` stores a snapshot, rewards at most one analysis per job (and not at all for
an invalid curve, a $0 job, a video hash already rewarded on another account, or a user at the
cap), and refuses duplicate curves and jobs that are not the caller's, done, shared and present.
`results.curve_quality` returns the failed checks, empty when the curve is valid. The route
validates its fields and writes nothing when S3 or the checks fail.

Money, snapshots and ledger rows are read directly from PostgreSQL (`pg`); S3 objects from moto.
"""

import json
import uuid

import pytest
from conftest import WEB_USER_ID
from neurolens import billing, storage
from neurolens.web.app import create_app

STARTER_CENTS = 10_000
PRICE_CENTS = 90  # 27.4 s, one started 30 s block
OTHER_USER = "other-user"
VIEWS = "lt_1k"
PUBLISHED = "week"


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
    """A descending curve whose watch ratio spread is first - last."""
    return [(i / (points - 1), first + (last - first) * i / (points - 1)) for i in range(points)]


def curve_args(job_id, *, sha="a" * 64, points=None, flags=None, **overrides):
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


def app_for(aws, db, make_cfg, tmp_path, s3_client=None):
    cfg = make_cfg(
        research={
            "curve_rewards_per_window": 5,
            "curve_reward_window_days": 30,
            "no_curve_days": 90,
        }
    )
    app = create_app(
        cfg=cfg,
        data_dir=tmp_path / "data",
        db=db,
        s3_client=s3_client or aws.s3,
    )
    app.config["TESTING"] = True
    return app.test_client()


class PutFails:
    """An S3 client whose every write fails, as S3 may."""

    def __init__(self, inner):
        self._inner = inner

    def put_object(self, **kwargs):
        raise RuntimeError("S3 write failed")

    def __getattr__(self, name):
        return getattr(self._inner, name)


# ---------------------------------------------------------------- curve_quality


def quality(points):
    from neurolens.results import curve_quality

    return curve_quality(points)


def test_a_good_curve_has_no_failed_checks():
    assert quality(curve()) == []


def test_twenty_points_is_enough_and_nineteen_is_not():
    assert quality(curve(20)) == []
    assert quality(curve(19))


def test_a_watch_ratio_spread_of_002_is_enough_and_less_is_not():
    assert quality(curve(first=1.0, last=0.98)) == []
    assert quality(curve(first=1.0, last=0.981))


def test_a_curve_that_ends_where_it_started_is_not_rising():
    points = curve(first=0.5, last=0.5)
    points[10] = (points[10][0], 0.52)  # a spread without rising overall
    assert quality(points) == []


def test_a_curve_that_ends_above_where_it_started_is_refused():
    assert quality(curve(first=0.5, last=0.55))


def test_a_watch_ratio_of_exactly_10_is_allowed_and_above_is_refused():
    assert quality(curve(first=10.0, last=5.0)) == []
    assert quality(curve(first=10.01, last=5.0))


# ---------------------------------------------------------------- add_curve: the reward


def test_a_valid_curve_rewards_the_captured_price_once(db, pg):
    job_id, _ = make_job(db)
    assert pg.balance() == (STARTER_CENTS - PRICE_CENTS, 0)

    result = billing.add_curve(db, WEB_USER_ID, job_id, **curve_args(job_id))

    assert result["rewarded_cents"] == PRICE_CENTS
    assert result["quality"] == []
    assert pg.balance() == (STARTER_CENTS, 0)
    ledger = pg.ledger()
    assert [row["kind"] for row in ledger] == ["starter", "reserve", "capture", "curve_reward"]
    reward = ledger[-1]
    assert reward["job_id"] == job_id
    assert (reward["available_delta_cents"], reward["reserved_delta_cents"]) == (PRICE_CENTS, 0)
    job = pg.job(job_id)
    assert job["curve_rewarded_at"] is not None
    [snapshot] = snapshot_rows(pg, job_id)
    assert snapshot["rewarded_cents"] == PRICE_CENTS
    assert snapshot["points"] == 20
    assert snapshot["views"] == VIEWS and snapshot["published"] == PUBLISHED
    assert snapshot["promoted"] is False


def test_a_second_curve_on_the_same_job_is_stored_without_another_reward(db, pg):
    job_id, _ = make_job(db)
    billing.add_curve(db, WEB_USER_ID, job_id, **curve_args(job_id))
    after_first = pg.snapshot()

    result = billing.add_curve(
        db, WEB_USER_ID, job_id, **curve_args(job_id, sha="b" * 64, points=curve(last=0.6))
    )

    assert result["rewarded_cents"] == 0
    assert pg.snapshot()["balances"] == after_first["balances"]
    assert pg.snapshot()["ledger"] == after_first["ledger"]
    assert len(snapshot_rows(pg, job_id)) == 2
    assert snapshot_rows(pg, job_id)[-1]["rewarded_cents"] == 0
    assert pg.kinds().count("curve_reward") == 1


def test_the_unique_index_refuses_a_second_curve_reward_row(db, pg):
    from psycopg.errors import UniqueViolation

    job_id, _ = make_job(db)
    billing.add_curve(db, WEB_USER_ID, job_id, **curve_args(job_id))
    balance = pg.balance()

    with pytest.raises(UniqueViolation):
        pg.conn.execute(
            "INSERT INTO ledger (user_id, job_id, kind, available_delta_cents, "
            "reserved_delta_cents) VALUES (%s, %s::uuid, 'curve_reward', 1, 0)",
            (WEB_USER_ID, job_id),
        )

    assert pg.balance() == balance


def test_an_invalid_curve_is_stored_without_a_reward(db, pg):
    job_id, _ = make_job(db)
    before = pg.balance()

    result = billing.add_curve(
        db,
        WEB_USER_ID,
        job_id,
        **curve_args(job_id, points=curve(5), flags=["too_few_points"]),
    )

    assert result["rewarded_cents"] == 0
    assert result["quality"] == ["too_few_points"]
    assert pg.balance() == before
    assert pg.job(job_id)["curve_rewarded_at"] is None
    [snapshot] = snapshot_rows(pg, job_id)
    assert snapshot["rewarded_cents"] == 0


def test_a_zero_price_job_is_not_rewarded(db, pg):
    job_id, _ = make_job(db)
    pg.conn.execute(
        "UPDATE jobs SET captured_cents = 0 WHERE job_id = %s::uuid", (job_id,)
    )
    balance = pg.balance()

    result = billing.add_curve(db, WEB_USER_ID, job_id, **curve_args(job_id))

    assert result["rewarded_cents"] == 0
    assert pg.balance() == balance
    assert "curve_reward" not in pg.kinds()


def test_a_video_already_rewarded_on_another_account_is_not_rewarded_again(db, pg):
    first, _ = make_job(db, sha="same-video")
    second, _ = make_job(db, user_id=OTHER_USER, sha="same-video")
    billing.add_curve(db, WEB_USER_ID, first, **curve_args(first))
    balance = pg.balance(OTHER_USER)

    result = billing.add_curve(
        db, OTHER_USER, second, **curve_args(second, sha="b" * 64, points=curve(last=0.6))
    )

    assert result["rewarded_cents"] == 0
    assert pg.balance(OTHER_USER) == balance
    assert pg.job(second)["curve_rewarded_at"] is None


def test_a_user_at_the_cap_is_not_rewarded_until_the_window_passes(db, pg):
    cap = 2
    jobs = [make_job(db, sha=f"video-{number}")[0] for number in range(4)]
    for job_id in jobs[:cap]:
        result = billing.add_curve(
            db,
            WEB_USER_ID,
            job_id,
            **curve_args(job_id, sha=f"curve-{jobs.index(job_id)}", reward_cap=cap),
        )
        assert result["rewarded_cents"] == PRICE_CENTS

    second_balance = pg.balance()
    third = billing.add_curve(
        db,
        WEB_USER_ID,
        jobs[2],
        **curve_args(jobs[2], sha="curve-third", reward_cap=cap),
    )
    assert third["rewarded_cents"] == 0
    assert pg.balance() == second_balance

    # The two earlier rewards fall out of the window; the fourth job is rewarded again.
    pg.conn.execute(
        "UPDATE jobs SET curve_rewarded_at = now() - make_interval(days => 31) "
        "WHERE user_id = %s AND curve_rewarded_at IS NOT NULL",
        (WEB_USER_ID,),
    )
    pg.conn.execute(
        "UPDATE ledger SET created_at = now() - make_interval(days => 31) "
        "WHERE user_id = %s AND kind = 'curve_reward'",
        (WEB_USER_ID,),
    )
    fourth = billing.add_curve(
        db,
        WEB_USER_ID,
        jobs[3],
        **curve_args(jobs[3], sha="curve-fourth", reward_cap=cap),
    )
    assert fourth["rewarded_cents"] == PRICE_CENTS
    assert pg.balance() == (STARTER_CENTS - PRICE_CENTS, 0)


def test_a_duplicate_curve_is_refused_and_stores_nothing(db, pg):
    first, _ = make_job(db)
    second, _ = make_job(db, sha="b" * 64)
    billing.add_curve(db, WEB_USER_ID, first, **curve_args(first, sha="the-curve"))
    before = pg.snapshot()

    with pytest.raises(billing.DuplicateCurve):
        billing.add_curve(db, WEB_USER_ID, second, **curve_args(second, sha="the-curve"))

    assert pg.snapshot() == before
    assert snapshot_rows(pg, second) == []


@pytest.mark.parametrize(
    "case",
    ["queued", "processing", "not_shared", "removed", "deleted", "someone_else"],
)
def test_add_curve_refuses_a_job_that_is_not_eligible(db, pg, case):
    if case == "someone_else":
        job_id, _ = make_job(db, user_id=OTHER_USER)
        caller = WEB_USER_ID
    elif case == "queued":
        job_id, _ = make_job(db, state="queued")
        caller = WEB_USER_ID
    elif case == "processing":
        job_id, _ = make_job(db, state="processing")
        caller = WEB_USER_ID
    elif case == "not_shared":
        job_id, _ = make_job(db, consent=False)
        caller = WEB_USER_ID
    elif case == "removed":
        job_id, _ = make_job(db)
        pg.conn.execute(
            "UPDATE jobs SET dataset_removed_at = now(), dataset_removed_reason = 'stopped' "
            "WHERE job_id = %s::uuid",
            (job_id,),
        )
        caller = WEB_USER_ID
    else:
        job_id, _ = make_job(db)
        pg.conn.execute("UPDATE jobs SET deleted_at = now() WHERE job_id = %s::uuid", (job_id,))
        caller = WEB_USER_ID

    add_curve = getattr(billing, "add_curve", None)
    assert callable(add_curve), "billing.add_curve is missing"
    before = pg.snapshot()
    with pytest.raises(Exception):  # noqa: B017 - the spec names no exception type here
        add_curve(db, caller, job_id, **curve_args(job_id))

    assert pg.snapshot() == before
    assert snapshot_rows(pg, job_id) == []


# ---------------------------------------------------------------- the retention route


def test_the_route_stores_the_raw_report_and_the_parsed_curve(db, aws, make_cfg, tmp_path, pg):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
    points = curve()
    report = report_text(points)

    resp = client.post(
        f"/api/jobs/{job_id}/retention",
        json={"report": report, "views": VIEWS, "published": PUBLISHED, "promoted": False},
    )

    assert 200 <= resp.status_code < 300
    [snapshot] = snapshot_rows(pg, job_id)
    assert snapshot["rewarded_cents"] == PRICE_CENTS
    assert pg.job(job_id)["curve_rewarded_at"] is not None
    raw = aws.s3.get_object(Bucket=aws.bucket, Key=snapshot["raw_key"])["Body"].read()
    assert json.loads(raw)["rows"] == [[elapsed, watch] for elapsed, watch in points]
    csv_text = aws.s3.get_object(Bucket=aws.bucket, Key=snapshot["csv_key"])["Body"].read()
    assert csv_text.decode().splitlines()[0] == "elapsed_ratio,watch_ratio"
    assert len(csv_text.decode().splitlines()) == 1 + len(points)
    assert pg.job(job_id)["retention_added_at"] is not None


@pytest.mark.parametrize(
    "change",
    [
        {"report": None},
        {"report": 123},
        {"views": "lt-1k"},
        {"views": None},
        {"published": "this_week"},
        {"published": None},
        {"promoted": "yes"},
        {"promoted": None},
    ],
    ids=[
        "report_null",
        "report_number",
        "views_bad",
        "views_missing",
        "published_bad",
        "published_missing",
        "promoted_bad",
        "promoted_missing",
    ],
)
def test_the_route_refuses_bad_fields_and_writes_nothing(db, aws, make_cfg, tmp_path, pg, change):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
    body = {
        "report": report_text(curve()),
        "views": VIEWS,
        "published": PUBLISHED,
        "promoted": False,
    }
    body.update(change)

    resp = client.post(f"/api/jobs/{job_id}/retention", json=body)

    assert resp.status_code == 400
    assert snapshot_rows(pg, job_id) == []
    assert pg.job(job_id)["retention_added_at"] is None
    assert not aws.s3.list_objects_v2(
        Bucket=aws.bucket, Prefix=f"dataset/{job_id}/retention/"
    ).get("Contents")


def test_an_s3_failure_records_nothing(db, aws, make_cfg, tmp_path, pg):
    s3 = PutFails(aws.s3)
    client = app_for(aws, db, make_cfg, tmp_path, s3_client=s3)
    job_id, _ = make_job(db)
    balance = pg.balance()

    resp = client.post(
        f"/api/jobs/{job_id}/retention",
        json={
            "report": report_text(curve()),
            "views": VIEWS,
            "published": PUBLISHED,
            "promoted": False,
        },
    )

    assert resp.status_code == 500
    assert snapshot_rows(pg, job_id) == []
    assert pg.job(job_id)["retention_added_at"] is None
    assert pg.balance() == balance
    assert "curve_reward" not in pg.kinds()


@pytest.mark.parametrize(
    "reason, message",
    [
        ("stopped", "You stopped sharing this video."),
        ("no_curve", "This video's research copy was deleted after 90 days without a curve."),
    ],
)
def test_a_removed_copy_says_why(db, aws, make_cfg, tmp_path, pg, reason, message):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
    pg.conn.execute(
        "UPDATE jobs SET dataset_removed_at = now(), dataset_removed_reason = %s "
        "WHERE job_id = %s::uuid",
        (reason, job_id),
    )

    resp = client.post(
        f"/api/jobs/{job_id}/retention",
        json={
            "report": report_text(curve()),
            "views": VIEWS,
            "published": PUBLISHED,
            "promoted": False,
        },
    )

    assert resp.status_code == 409
    assert message in resp.get_json()["message"]
    assert snapshot_rows(pg, job_id) == []
