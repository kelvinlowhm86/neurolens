"""M3j web changes (docs/M3j_spec.md §2, §4, §6, §8, §9, §9b, §11).

The check route looks the video up before any work and names every reward reason; the save route
does its own lookup and stores the checked facts instead of the questions. The launcher reads the
YouTube key from Parameter Store and fails to start without it. The pages carry the new wording,
the worker stops writing the course-study copy, and `purge_academic.py` removes only `academic/`
keys unless asked to act.
"""

import contextlib
import importlib
import importlib.util
import json
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from botocore.exceptions import ClientError
from conftest import REPO_ROOT, STARTER_CENTS, WEB_USER_ID, fresh_import
from neurolens import billing, pricing, settings, storage
from neurolens.web.app import create_app

PRICE_CENTS = pricing.estimate_cost_cents(30.1)
OTHER_USER = "other-user"
VIEWS = "lt_1k"
PUBLISHED = "week"
REWARD_MIN_DAYS = 7
LENGTH_TOLERANCE_S = 2
DAY_S = 24 * 60 * 60
YOUTUBE_ID = "dQw4w9WgXcQ"
YOUTUBE_LINK = f"https://www.youtube.com/watch?v={YOUTUBE_ID}"
YOUTUBE_REF = f"youtube:{YOUTUBE_ID}"
FACEBOOK_ID = "1234567890"
FACEBOOK_LINK = f"https://www.facebook.com/MyPage/videos/{FACEBOOK_ID}"
FACEBOOK_REF = f"facebook:{FACEBOOK_ID}"

PURGE_SCRIPT = REPO_ROOT / "infra" / "purge_academic.py"


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


def days_ago(days):
    return datetime.now(UTC) - timedelta(days=days)


def facts(published_at, *, duration_s=31, views=25048):
    from neurolens.youtube import VideoFacts

    return VideoFacts(published_at=published_at, duration_s=duration_s, views=views)


def patch_lookup(monkeypatch, result):
    """Fake `neurolens.youtube.lookup` as the routes call it (an exception is raised)."""
    from neurolens import youtube

    def lookup(video_id, api_key, *, timeout=5):
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(youtube, "lookup", lookup)


def video_ref_for(job_id):
    return f"youtube:{job_id.replace('-', '')[:11]}"


def curve_args(job_id, *, sha="a" * 64, points=None, video_ref=None, facts_=None):
    snapshot_id = str(uuid.uuid4())
    return {
        "snapshot_id": snapshot_id,
        "raw_key": f"dataset/{job_id}/retention/{snapshot_id}.json",
        "csv_key": f"dataset/{job_id}/retention/{snapshot_id}.csv",
        "curve_sha256": sha,
        "points": points if points is not None else curve(),
        "quality_flags": [],
        "views": None if facts_ is not None else VIEWS,
        "published": None if facts_ is not None else PUBLISHED,
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


def app_for(aws, db, make_cfg, tmp_path, *, research=None, youtube_api_key="test-youtube-key"):
    research_cfg = {
        "curve_rewards_per_window": 5,
        "curve_reward_window_days": 30,
        "curve_reward_min_days": REWARD_MIN_DAYS,
        "curve_length_tolerance_s": LENGTH_TOLERANCE_S,
        "no_curve_days": 90,
    }
    research_cfg.update(research or {})
    cfg = make_cfg(research=research_cfg, youtube_api_key=youtube_api_key)
    app = create_app(
        cfg=cfg,
        data_dir=tmp_path / "data",
        db=db,
        s3_client=aws.s3,
    )
    app.config["TESTING"] = True
    return app.test_client()


def check_body(**overrides):
    body = {"video_url": YOUTUBE_LINK}
    body.update(overrides)
    return body


def retention_body(**overrides):
    body = {
        "report": report_text(curve()),
        "video_url": YOUTUBE_LINK,
        "same_cut": "same",
        "promoted": False,
    }
    body.update(overrides)
    return body


def retention_keys(aws, job_id):
    prefix = f"dataset/{job_id}/retention/"
    return aws.s3.list_objects_v2(Bucket=aws.bucket, Prefix=prefix).get("Contents", [])


def _section_text(html, anchor):
    start = re.search(rf'<h2[^>]*id="{anchor}"[^>]*>', html)
    assert start, f'the page has no <h2 id="{anchor}"> section'
    rest = html[start.end() :]
    end = re.search(r"<h2\b", rest)
    section = rest[: end.start()] if end else rest
    return " ".join(re.sub(r"<[^>]+>", " ", section).split())


# ---------------------------------------------------------------- the check route


def test_the_check_reports_a_video_public_for_a_month_as_payable_now(
    db, aws, make_cfg, tmp_path, monkeypatch
):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    published = days_ago(30)
    patch_lookup(monkeypatch, facts(published))

    resp = client.post(f"/api/jobs/{job_id}/retention/check", json=check_body())

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["platform"] == "youtube"
    assert data["video_id"] == YOUTUBE_ID
    assert data["reward_cents"] == PRICE_CENTS
    assert data["reward_from"] is None
    assert data["reason"] is None
    assert data["published_on"] == published.date().isoformat()


def test_the_check_reports_a_young_video_with_the_date_it_pays(
    db, aws, make_cfg, tmp_path, monkeypatch, pg
):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
    pg.age(job_id, created_s=10 * DAY_S)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    published = days_ago(2)
    patch_lookup(monkeypatch, facts(published))

    resp = client.post(f"/api/jobs/{job_id}/retention/check", json=check_body())

    expected_from = storage.utc_text(published + timedelta(days=REWARD_MIN_DAYS))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["reason"] == "too_early"
    assert data["reward_from"] == expected_from
    assert data["reward_cents"] == PRICE_CENTS
    assert data["published_on"] == published.date().isoformat()


def test_the_check_reports_a_length_mismatch(db, aws, make_cfg, tmp_path, monkeypatch):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    patch_lookup(monkeypatch, facts(days_ago(30), duration_s=33))

    resp = client.post(f"/api/jobs/{job_id}/retention/check", json=check_body())

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["reason"] == "length_mismatch"
    assert data["reward_cents"] == 0
    assert data["reward_from"] is None


def test_the_check_reports_an_unfunded_account(db, aws, make_cfg, tmp_path, monkeypatch):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
    patch_lookup(monkeypatch, facts(days_ago(30)))

    resp = client.post(f"/api/jobs/{job_id}/retention/check", json=check_body())

    assert resp.status_code == 200
    assert resp.get_json()["reason"] == "not_funded"
    assert resp.get_json()["reward_cents"] == 0


def test_the_check_reports_an_uncharged_job(db, aws, make_cfg, tmp_path, monkeypatch, pg):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    pg.conn.execute("UPDATE jobs SET captured_cents = 0 WHERE job_id = %s::uuid", (job_id,))
    patch_lookup(monkeypatch, facts(days_ago(30)))

    resp = client.post(f"/api/jobs/{job_id}/retention/check", json=check_body())

    assert resp.status_code == 200
    assert resp.get_json()["reason"] == "not_charged"


def test_the_check_reports_an_already_rewarded_job(db, aws, make_cfg, tmp_path, monkeypatch, pg):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    pg.conn.execute("UPDATE jobs SET curve_rewarded_at = now() WHERE job_id = %s::uuid", (job_id,))
    patch_lookup(monkeypatch, facts(days_ago(30)))

    resp = client.post(f"/api/jobs/{job_id}/retention/check", json=check_body())

    assert resp.status_code == 200
    assert resp.get_json()["reason"] == "already_rewarded"


def test_the_check_reports_the_window_cap(db, aws, make_cfg, tmp_path, monkeypatch):
    client = app_for(aws, db, make_cfg, tmp_path, research={"curve_rewards_per_window": 0})
    job_id, _ = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    patch_lookup(monkeypatch, facts(days_ago(30)))

    resp = client.post(f"/api/jobs/{job_id}/retention/check", json=check_body())

    assert resp.status_code == 200
    assert resp.get_json()["reason"] == "cap_reached"
    assert resp.get_json()["reward_cents"] == 0


def test_a_video_we_cannot_see_is_400_and_writes_nothing(
    db, aws, make_cfg, tmp_path, monkeypatch, pg
):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    before = pg.snapshot()
    patch_lookup(monkeypatch, None)

    resp = client.post(f"/api/jobs/{job_id}/retention/check", json=check_body())

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "video_not_found"
    assert resp.get_json()["message"] == (
        "We can't see this video. Check the link, or add it once it's public."
    )
    assert snapshot_rows(pg, job_id) == []
    assert retention_keys(aws, job_id) == []
    assert pg.snapshot() == before


def test_a_failed_lookup_falls_back_to_the_analysis_date_and_logs(
    db, aws, make_cfg, tmp_path, monkeypatch, pg, caplog
):
    from neurolens import youtube

    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
    pg.age(job_id, created_s=6 * DAY_S)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    patch_lookup(monkeypatch, youtube.LookupFailed("the video lookup failed"))

    with caplog.at_level("ERROR"):
        resp = client.post(f"/api/jobs/{job_id}/retention/check", json=check_body())

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["reason"] == "too_early"
    assert data["reward_from"] == reward_date(pg, job_id)
    assert data["published_on"] is None
    assert any(record.levelname == "ERROR" for record in caplog.records)
    assert snapshot_rows(pg, job_id) == []


def test_the_check_refuses_a_link_taken_by_another_account(
    db, aws, make_cfg, tmp_path, monkeypatch, pg
):
    client = app_for(aws, db, make_cfg, tmp_path)
    other, _ = make_job(db, user_id=OTHER_USER)
    pg.age(other, created_s=8 * DAY_S)
    billing.grant_credit(db, f"{OTHER_USER}@example.com", 100)
    billing.add_curve(
        db,
        OTHER_USER,
        other,
        **curve_args(other, facts_=facts(days_ago(30)), video_ref=YOUTUBE_REF),
    )
    job_id, _ = make_job(db)
    patch_lookup(monkeypatch, facts(days_ago(30)))

    resp = client.post(f"/api/jobs/{job_id}/retention/check", json=check_body())

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "video_link_taken"
    assert snapshot_rows(pg, job_id) == []


def test_the_check_refuses_a_job_already_linked_to_another_video(
    db, aws, make_cfg, tmp_path, monkeypatch, pg
):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
    billing.add_curve(
        db,
        WEB_USER_ID,
        job_id,
        **curve_args(job_id, facts_=facts(days_ago(30)), video_ref=FACEBOOK_REF),
    )
    patch_lookup(monkeypatch, facts(days_ago(30)))

    resp = client.post(f"/api/jobs/{job_id}/retention/check", json=check_body())

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "video_link_changed"


# ---------------------------------------------------------------- the save route


def test_the_save_stores_the_checked_facts_and_nulls_the_questions(
    db, aws, make_cfg, tmp_path, monkeypatch, pg
):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
    published = days_ago(30)
    patch_lookup(monkeypatch, facts(published))

    resp = client.post(f"/api/jobs/{job_id}/retention", json=retention_body())

    assert resp.status_code == 200
    [snapshot] = snapshot_rows(pg, job_id)
    assert snapshot["checked_views"] == 25048
    assert snapshot["video_seconds"] == 31
    assert abs((snapshot["published_at"] - published).total_seconds()) < 1
    assert snapshot["views"] is None
    assert snapshot["published"] is None


@pytest.mark.parametrize("field", ["views", "published"])
def test_the_save_rejects_an_answer_the_check_supplied(
    db, aws, make_cfg, tmp_path, monkeypatch, pg, field
):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
    patch_lookup(monkeypatch, facts(days_ago(30)))
    value = VIEWS if field == "views" else PUBLISHED

    resp = client.post(f"/api/jobs/{job_id}/retention", json=retention_body(**{field: value}))

    assert resp.status_code == 400
    assert snapshot_rows(pg, job_id) == []
    assert retention_keys(aws, job_id) == []


@pytest.mark.parametrize("answered", [True, False], ids=["answered", "not_answered"])
def test_without_facts_the_questions_are_optional_weights(
    db, aws, make_cfg, tmp_path, monkeypatch, pg, answered
):
    """A lookup that fails after the check hid the questions must still save (M3j §6)."""
    from neurolens import youtube

    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
    patch_lookup(monkeypatch, youtube.LookupFailed("the video lookup failed"))
    body = retention_body(views=VIEWS, published=PUBLISHED) if answered else retention_body()

    saved = client.post(f"/api/jobs/{job_id}/retention", json=body)

    assert saved.status_code == 200
    [snapshot] = snapshot_rows(pg, job_id)
    assert snapshot["checked_views"] is None
    assert snapshot["published_at"] is None
    assert snapshot["video_seconds"] is None
    assert snapshot["views"] == (VIEWS if answered else None)
    assert snapshot["published"] == (PUBLISHED if answered else None)


def test_without_facts_a_bad_answer_is_still_refused(db, aws, make_cfg, tmp_path, monkeypatch, pg):
    from neurolens import youtube

    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)
    patch_lookup(monkeypatch, youtube.LookupFailed("the video lookup failed"))

    resp = client.post(f"/api/jobs/{job_id}/retention", json=retention_body(views="lots"))

    assert resp.status_code == 400
    assert snapshot_rows(pg, job_id) == []
    assert retention_keys(aws, job_id) == []


def test_a_facebook_link_needs_the_questions_and_stores_no_checked_facts(
    db, aws, make_cfg, tmp_path, pg
):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = make_job(db)

    resp = client.post(
        f"/api/jobs/{job_id}/retention",
        json=retention_body(
            report=facebook_report(),
            video_url=FACEBOOK_LINK,
            views=VIEWS,
            published=PUBLISHED,
        ),
    )

    assert resp.status_code == 200
    [snapshot] = snapshot_rows(pg, job_id)
    assert snapshot["video_ref"] == FACEBOOK_REF
    assert snapshot["checked_views"] is None
    assert snapshot["published_at"] is None
    assert snapshot["video_seconds"] is None


# ---------------------------------------------------------------- settings


ENV_MAP = {
    "NEUROLENS_YOUTUBE_API_KEY": ("youtube_api_key",),
}


@pytest.mark.parametrize("name, path", list(ENV_MAP.items()), ids=list(ENV_MAP))
def test_apply_env_copies_the_youtube_api_key(monkeypatch, name, path):
    monkeypatch.setenv(name, "the-video-key")

    out = settings.apply_env({})

    node = out
    for part in path:
        node = node[part]
    assert node == "the-video-key"


def test_the_deployed_launcher_reads_the_youtube_key_from_parameter_store(
    lambda_env, ssm, monkeypatch
):
    ssm.put_parameter(
        Name="/neurolens/web/youtube_api_key", Value="the-video-key", Type="SecureString"
    )
    module = fresh_import("neurolens.web.lambda_handler")

    cfg = module._load_settings()

    assert cfg["youtube_api_key"] == "the-video-key"


def test_the_deployed_launcher_fails_to_start_without_the_youtube_key(lambda_env):
    module = fresh_import("neurolens.web.lambda_handler")

    with pytest.raises(ClientError) as exc:
        module._load_settings()

    assert exc.value.response["Error"]["Code"] == "ParameterNotFound"


def test_the_youtube_key_never_appears_in_a_reply(db, aws, make_cfg, tmp_path, monkeypatch):
    secret = "never-show-this-key"
    client = app_for(aws, db, make_cfg, tmp_path, youtube_api_key=secret)
    job_id, _ = make_job(db)
    billing.grant_credit(db, f"{WEB_USER_ID}@example.com", 100)
    patch_lookup(monkeypatch, facts(days_ago(30)))

    check = client.post(f"/api/jobs/{job_id}/retention/check", json=check_body())
    saved = client.post(f"/api/jobs/{job_id}/retention", json=retention_body())

    assert secret not in check.get_data(as_text=True)
    assert secret not in saved.get_data(as_text=True)


# ---------------------------------------------------------------- copy


def test_the_page_and_the_server_share_the_new_consent_version():
    assert billing.CONSENT_VERSION == "2026-11-03"

    html = (REPO_ROOT / "static" / "app.html").read_text()
    match = re.search(r"const\s+CONSENT_VERSION\s*=\s*['\"](?P<version>[^'\"]+)['\"]", html)
    assert match, "static/app.html has no `const CONSENT_VERSION = '...'` literal"
    assert match.group("version") == billing.CONSENT_VERSION


def test_the_privacy_page_states_the_configured_week_and_length_tolerance():
    research = settings.load_settings()["research"]
    days = research["curve_reward_min_days"]
    tolerance = research["curve_length_tolerance_s"]
    assert days == REWARD_MIN_DAYS
    assert tolerance == LENGTH_TOLERANCE_S

    html = (REPO_ROOT / "static" / "privacy.html").read_text()
    text = _section_text(html, "sharing")

    assert f"public at least {days} days" in text
    assert f"give or take {tolerance} seconds" in text
    assert 'id="research"' in html, "the old #research anchor must still land on the section"


@pytest.mark.parametrize("page", ["app.html", "index.html", "privacy.html", "retention_guide.html"])
def test_no_page_still_says_for_research(page):
    html = (REPO_ROOT / "static" / page).read_text()
    text = " ".join(re.sub(r"<[^>]+>", " ", html).split()).lower()

    assert "for research" not in text


# ---------------------------------------------------------------- the worker's copy


@pytest.fixture
def fake_worker(monkeypatch):
    monkeypatch.setenv("FAKE_INFERENCE", "1")
    from neurolens import inference, worker

    monkeypatch.setattr(inference, "gpu_info", lambda: None)
    monkeypatch.setattr(worker, "gpu_info", lambda: None, raising=False)


def worker_job(db, *, consent=True):
    from conftest import USER_ID

    job_id = str(uuid.uuid4())
    key = f"uploads/{USER_ID}/{job_id}.mp4"
    billing.ensure_user(db, USER_ID, f"{USER_ID}@example.com", STARTER_CENTS)
    billing.reserve(db, USER_ID, job_id, key, "clip.mp4", 3000, research_consent=consent)
    return job_id, key


def run_worker(aws, db, job_id, clip_path, cfg, masks):
    from neurolens import settings, worker

    assert billing.verify_queued(db, job_id, 3000, settings.max_duration(None)) == "ok"
    return worker.handle_job(
        job_id,
        str(clip_path),
        s3=aws.s3,
        db=db,
        cfg=cfg,
        roi_masks=masks,
        keep_alive=lambda on_beat: contextlib.nullcontext(),
    )


def prefix_keys(aws, prefix):
    pages = aws.s3.get_paginator("list_objects_v2").paginate(Bucket=aws.bucket, Prefix=prefix)
    return [obj["Key"] for page in pages for obj in page.get("Contents", [])]


def test_a_consented_job_writes_the_video_under_dataset_and_no_academic_copy(
    aws, db, make_cfg, roi_masks_small, clip_path, fake_worker
):
    from neurolens import worker

    job_id, key = worker_job(db)
    aws.s3.upload_file(str(clip_path), aws.bucket, key)

    outcome = run_worker(aws, db, job_id, clip_path, make_cfg(), roi_masks_small)

    assert outcome is worker.Outcome.DONE
    assert prefix_keys(aws, f"dataset/{job_id}/") == [f"dataset/{job_id}/video.mp4"]
    assert prefix_keys(aws, f"academic/{job_id}/") == []
    assert storage.object_exists(aws.s3, aws.bucket, f"results/{job_id}.json")
    assert storage.object_exists(aws.s3, aws.bucket, f"results/{job_id}.brain")


# ---------------------------------------------------------------- purge_academic.py


def load_purge():
    spec = importlib.util.spec_from_file_location("purge_academic", PURGE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seed_bucket(aws):
    keys = [
        "academic/job-one/result.json",
        "academic/job-one/brain",
        "academic/job-two/result.json",
        "dataset/job-one/video.mp4",
        "results/job-one.json",
    ]
    for key in keys:
        aws.s3.put_object(Bucket=aws.bucket, Key=key, Body=b"x")
    return keys


def purge_env(monkeypatch, aws):
    monkeypatch.setenv("NEUROLENS_S3_BUCKET", aws.bucket)
    monkeypatch.setenv("NEUROLENS_AWS_REGION", "us-east-1")


def test_purge_academic_dry_run_deletes_nothing(aws, monkeypatch):
    purge_env(monkeypatch, aws)
    keys = seed_bucket(aws)
    module = load_purge()

    module.main([])

    assert all(storage.object_exists(aws.s3, aws.bucket, key) for key in keys)


def test_purge_academic_delete_removes_only_academic_keys(aws, monkeypatch):
    purge_env(monkeypatch, aws)
    seed_bucket(aws)
    aws.s3.create_bucket(Bucket="another-bucket")
    aws.s3.put_object(Bucket="another-bucket", Key="academic/job/result.json", Body=b"x")
    module = load_purge()

    module.main(["--delete"])

    assert not storage.object_exists(aws.s3, aws.bucket, "academic/job-one/result.json")
    assert not storage.object_exists(aws.s3, aws.bucket, "academic/job-one/brain")
    assert not storage.object_exists(aws.s3, aws.bucket, "academic/job-two/result.json")
    assert storage.object_exists(aws.s3, aws.bucket, "dataset/job-one/video.mp4")
    assert storage.object_exists(aws.s3, aws.bucket, "results/job-one.json")
    assert storage.object_exists(aws.s3, "another-bucket", "academic/job/result.json")
