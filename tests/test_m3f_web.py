"""M3f web changes (docs/M3f_spec.md §3, §4 item 7, §5d, §7, §10 "claim", "CSV header", "app.py").

`claim` records `claimed_at`; `list_jobs` leaves out cancelled jobs and returns `claimed_at`,
`cancellable`, `sharing`, `curve_count` and `curve_reward_cents`. The CSV is named after the
video. `python app.py` refuses to start unless `AWS_ENDPOINT_URL` points at the local fake stack.
"""

import json
import os
import subprocess
import sys
import urllib.parse
import uuid

import pytest
from conftest import REPO_ROOT, STARTER_CENTS, WEB_USER_ID
from neurolens import billing, storage
from neurolens.web.app import create_app

SERIES = [
    "faces",
    "bodies",
    "scenes",
    "visual",
    "sound",
    "speech",
    "social",
    "see",
    "hear",
    "understand",
    "overall",
]
PRICE_CENTS = 90
REFUSAL = (
    "python app.py runs only against the local fake stack: python infra/local_stack.py. "
    "The real .env is for the admin scripts."
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


def make_job(
    db,
    *,
    filename="My Ad.mp4",
    state="done",
    consent=False,
    sha=None,
    starter_cents=STARTER_CENTS,
):
    billing.ensure_user(db, WEB_USER_ID, f"{WEB_USER_ID}@example.com", starter_cents)
    job_id = str(uuid.uuid4())
    key = storage.object_key(WEB_USER_ID, job_id, "video/mp4")
    billing.reserve(db, WEB_USER_ID, job_id, key, filename, 27_400, research_consent=consent)
    if state in ("processing", "done", "failed"):
        assert billing.claim(db, job_id) == 1
    if state == "done":
        assert billing.settle_success(db, job_id) is True
    elif state == "failed":
        assert billing.issue_refund(db, job_id, "unreadable_video", attempt=1) is True
    if sha is not None:
        billing.record_video_hash(db, job_id, sha)
    return job_id, key


def result_for(job_id):
    return {
        "format": 2,
        "duration_seconds": 1,
        "signals": {name: [0.1] for name in SERIES},
        "muted": {name: [0.05] for name in SERIES},
        "job_id": job_id,
        "processing_time_seconds": 12.3,
    }


def done_job_with_result(aws, db, **kwargs):
    job_id, key = make_job(db, **kwargs)
    assert storage.put_result(aws.s3, aws.bucket, job_id, result_for(job_id)) is True
    return job_id, key


def curve():
    return [(i / 19, 1.0 - 0.5 * i / 19) for i in range(20)]


def add_curve(db, job_id, *, sha="curve-sha"):
    snapshot_id = str(uuid.uuid4())
    return billing.add_curve(
        db,
        WEB_USER_ID,
        job_id,
        snapshot_id=snapshot_id,
        raw_key=f"dataset/{job_id}/retention/{snapshot_id}.json",
        csv_key=f"dataset/{job_id}/retention/{snapshot_id}.csv",
        curve_sha256=sha,
        points=curve(),
        quality_flags=[],
        views="lt_1k",
        published="week",
        promoted=False,
        reward_cap=5,
        reward_window_days=30,
    )


# ---------------------------------------------------------------- claimed_at


def test_claim_sets_claimed_at_on_every_successful_claim(db, pg):
    job_id, _ = make_job(db, state="queued")
    assert pg.job(job_id)["claimed_at"] is None

    assert billing.claim(db, job_id) == 1
    first = pg.job(job_id)["claimed_at"]
    assert first is not None

    pg.age(job_id, updated_s=120)  # a dead worker's claim
    assert billing.claim(db, job_id) == 2
    assert pg.job(job_id)["claimed_at"] > first


def test_list_jobs_returns_claimed_at_as_utc_text(db, pg):
    job_id, _ = make_job(db, state="processing")

    [row] = billing.list_jobs(db, WEB_USER_ID, 50)

    assert row["cancellable"] is False
    assert isinstance(row["claimed_at"], str)
    assert row["claimed_at"] == storage.utc_text(pg.job(job_id)["claimed_at"])


# ---------------------------------------------------------------- list_jobs fields


def test_list_jobs_returns_the_sharing_and_curve_fields(db, pg):
    job_id, _ = make_job(db, consent=True, sha="a-video")
    [row] = billing.list_jobs(db, WEB_USER_ID, 50)
    assert row["sharing"] is True
    assert row["curve_count"] == 0
    assert row["curve_reward_cents"] == PRICE_CENTS

    assert add_curve(db, job_id)["rewarded_cents"] == PRICE_CENTS

    [row] = billing.list_jobs(db, WEB_USER_ID, 50)
    assert row["sharing"] is True
    assert row["curve_count"] == 1
    assert row["curve_reward_cents"] == 0


def test_an_unshared_job_is_not_sharing(db):
    job_id, _ = make_job(db, consent=False)
    [row] = billing.list_jobs(db, WEB_USER_ID, 50)
    assert row["sharing"] is False
    assert row["curve_reward_cents"] == 0
    assert job_id


def test_a_removed_job_is_not_sharing(db, pg):
    job_id, _ = make_job(db, consent=True, sha="a-video")
    pg.conn.execute(
        "UPDATE jobs SET dataset_removed_at = now(), dataset_removed_reason = 'stopped' "
        "WHERE job_id = %s::uuid",
        (job_id,),
    )

    [row] = billing.list_jobs(db, WEB_USER_ID, 50)

    assert row["sharing"] is False
    assert row["curve_reward_cents"] == 0


# ---------------------------------------------------------------- cancelled jobs


def test_list_jobs_and_the_jobs_api_leave_out_cancelled_jobs(aws, db, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    cancelled, _ = make_job(db, state="queued")
    assert billing.issue_refund(db, cancelled, "cancelled", queued=True) is True
    failed, _ = make_job(db, state="failed")
    done, _ = make_job(db, state="done")

    listed = {str(row["job_id"]) for row in billing.list_jobs(db, WEB_USER_ID, 50)}
    api = {row["job_id"] for row in client.get("/api/jobs").get_json()}

    assert cancelled not in listed and cancelled not in api
    assert {failed, done} <= listed
    assert {failed, done} <= api


# ---------------------------------------------------------------- the CSV name


def test_csv_is_named_after_the_video(aws, db, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = done_job_with_result(aws, db, filename="My Ad.mp4")

    resp = client.get(f"/api/jobs/{job_id}/result.csv")

    assert resp.status_code == 200
    assert resp.headers["Content-Disposition"] == (
        'attachment; filename="My Ad \u2013 NeuroLens.csv"'
    )


def test_a_non_ascii_name_gets_an_rfc_5987_filename(aws, db, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = done_job_with_result(aws, db, filename="Caf\u00e9 ad.mp4")

    resp = client.get(f"/api/jobs/{job_id}/result.csv")

    header = resp.headers["Content-Disposition"]
    assert "filename*=UTF-8''" in header
    encoded = header.split("filename*=UTF-8''", 1)[1].strip().strip('"')
    assert urllib.parse.unquote(encoded) == "Caf\u00e9 ad \u2013 NeuroLens.csv"


def test_a_missing_name_falls_back_to_neurolens_result(aws, db, make_cfg, tmp_path):
    client = app_for(aws, db, make_cfg, tmp_path)
    job_id, _ = done_job_with_result(aws, db, filename=None)

    resp = client.get(f"/api/jobs/{job_id}/result.csv")

    header = resp.headers["Content-Disposition"]
    assert "NeuroLens result" in header
    assert header.endswith('.csv"')


# ---------------------------------------------------------------- python app.py


def run_app(tmp_path, endpoint):
    root = tmp_path / f"root-{endpoint or 'unset'}".replace("/", "_").replace(":", "_")
    (root / "data").mkdir(parents=True)
    (root / "models").mkdir()
    config = {
        "paths": {"models": str(root / "models"), "data": str(root / "data")},
        "response": {"colour_range": 0.5},
        "auth": {"mode": "dev", "dev_user_id": "u", "dev_email": "u@example.com"},
        "server": {"host": "127.0.0.1", "port": 0},
        "max_upload_bytes": 1000,
    }
    (root / "config.json").write_text(json.dumps(config))
    env = os.environ.copy()
    env["NEUROLENS_ROOT"] = str(root)
    env.pop("AWS_ENDPOINT_URL", None)
    if endpoint is not None:
        env["AWS_ENDPOINT_URL"] = endpoint
    try:
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "app.py")],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"app.py started with AWS_ENDPOINT_URL={endpoint!r}")


@pytest.mark.parametrize(
    "endpoint", [None, "https://s3.us-east-1.amazonaws.com"], ids=["unset", "remote"]
)
def test_app_py_refuses_without_a_local_endpoint(tmp_path, endpoint):
    result = run_app(tmp_path, endpoint)

    assert result.returncode != 0
    assert REFUSAL in result.stdout + result.stderr
