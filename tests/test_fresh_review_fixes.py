"""Fresh review fixes (docs/M3c_spec.md §5, §8; docs/M3f_spec.md §5c, §5d, §6b, §7, §10).

Account deletion marks first and purges later; the reward preview matches what `add_curve` pays;
`sharing` needs a done job; a stopped-then-deleted copy gets its own files purged; snapshots go
with the copy and with age; the retention route removes a file it wrote when the second write
fails; `/app` renews the sign-in hint; `app.py` reads `.env` before its local-only guard; and the
local stack leaves a failed dispatch on the queue.
"""

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath

import pytest
from botocore.exceptions import ClientError
from conftest import ISSUER, REPO_ROOT, WEB_USER_ID, sign_in
from neurolens import billing, storage
from neurolens.web.app import create_app
from test_m3f_purge_retry import run_reaper

STARTER = 10_000
USER = "fresh-user"
EMAIL = "fresh@example.com"
OTHER = "fresh-other"
DAY = 24 * 60 * 60
HINT_COOKIE = "nl_signed_in"
VIEWS = "lt_1k"
PUBLISHED = "week"


# ---------------------------------------------------------------- helpers


def make_user(db, user_id=USER, *, email=None, starter=STARTER):
    billing.ensure_user(db, user_id, email or f"{user_id}@example.com", starter)
    return user_id


def make_job(
    db,
    user_id=USER,
    *,
    state="done",
    consent=True,
    seconds=27.4,
    sha=None,
    filename="clip.mp4",
):
    job_id = str(uuid.uuid4())
    key = storage.object_key(user_id, job_id, "video/mp4")
    billing.reserve(
        db, user_id, job_id, key, filename, round(seconds * 1000), research_consent=consent
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


def seed_files(aws, job_id, key):
    """The research prefixes plus the user's own upload, result, brain and thumbnail."""
    ext = PurePosixPath(key).suffix
    research = [
        f"dataset/{job_id}/video{ext}",
        f"dataset/{job_id}/result.json",
        f"academic/{job_id}/result.json",
        f"academic/{job_id}/brain",
    ]
    own = [key, f"results/{job_id}.json", f"results/{job_id}.brain", f"results/{job_id}.jpg"]
    for name in [*research, *own]:
        aws.s3.put_object(Bucket=aws.bucket, Key=name, Body=b"x")
    return research, own


def object_exists(aws, key):
    try:
        aws.s3.head_object(Bucket=aws.bucket, Key=key)
    except ClientError as err:
        assert err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound")
        return False
    return True


def prefix_keys(aws, prefix):
    pages = aws.s3.get_paginator("list_objects_v2").paginate(Bucket=aws.bucket, Prefix=prefix)
    return [obj["Key"] for page in pages for obj in page.get("Contents", [])]


def curve(points=20, *, first=1.0, last=0.5):
    return [(i / (points - 1), first + (last - first) * i / (points - 1)) for i in range(points)]


def report_text(points=None):
    points = curve() if points is None else points
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


def add_curve(db, user_id, job_id, sha, *, cap=5, window=30):
    snapshot_id = str(uuid.uuid4())
    return billing.add_curve(
        db,
        user_id,
        job_id,
        snapshot_id=snapshot_id,
        raw_key=f"dataset/{job_id}/retention/{snapshot_id}.json",
        csv_key=f"dataset/{job_id}/retention/{snapshot_id}.csv",
        curve_sha256=sha,
        points=curve(),
        quality_flags=[],
        views=VIEWS,
        published=PUBLISHED,
        promoted=False,
        reward_cap=cap,
        reward_window_days=window,
    )


def preview(db, user_id, job_id, *, cap=5, window=30):
    rows = billing.list_jobs(db, user_id, 20, reward_cap=cap, reward_window_days=window)
    return next(row for row in rows if str(row["job_id"]) == job_id)


def snapshot_rows(pg, job_id):
    return pg.rows("SELECT * FROM retention_snapshots WHERE job_id = %s::uuid", job_id)


@pytest.fixture
def reaper_env(aws, db, monkeypatch, tmp_path, patch_everywhere):
    """The reaper handler on the test database, with config.json's research block configurable."""
    monkeypatch.setenv(
        "NEUROLENS_DB_CLUSTER_ARN", "arn:aws:rds:us-east-1:000000000000:cluster:neurolens-db"
    )
    monkeypatch.setenv(
        "NEUROLENS_DB_SECRET_ARN",
        "arn:aws:secretsmanager:us-east-1:000000000000:secret:rds!cluster-test",
    )
    monkeypatch.setenv("NEUROLENS_DB_NAME", "neurolens")
    monkeypatch.setenv("NEUROLENS_S3_BUCKET", aws.bucket)
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    def data_api_database(*args, **kwargs):
        return db

    patch_everywhere(
        "DataApiDatabase", data_api_database, "neurolens.db", "neurolens.lambdas.reaper"
    )

    def install(no_curve_days):
        root = tmp_path / f"root-{no_curve_days}"
        (root / "data").mkdir(parents=True)
        config = {"research": {"no_curve_days": no_curve_days}}
        (root / "config.json").write_text(json.dumps(config))
        monkeypatch.setenv("NEUROLENS_ROOT", str(root))

    return install


@pytest.fixture
def app_for(aws, db, make_cfg, tmp_path):
    def make(s3_client=None):
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

    return make


class DeleteReportsErrors:
    """A real S3 client whose deletes fail for every key, as S3 may."""

    def __init__(self, inner):
        self._inner = inner

    def delete_objects(self, **kwargs):
        objects = kwargs.get("Delete", {}).get("Objects", [])
        return {
            "Errors": [{"Key": obj["Key"], "Code": "InternalError"} for obj in objects],
            "Deleted": [],
        }

    def delete_object(self, **kwargs):
        raise RuntimeError("delete failed")

    def __getattr__(self, name):
        return getattr(self._inner, name)


class SecondPutFails:
    """An S3 client whose second write fails, as S3 may, so the first is left orphaned."""

    def __init__(self, inner):
        self._inner = inner
        self.puts = 0

    def put_object(self, **kwargs):
        self.puts += 1
        if self.puts == 2:
            raise RuntimeError("S3 write failed")
        return self._inner.put_object(**kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


# ---------------------------------------------------------------- A. account delete marks first


def test_delete_account_marks_the_account_and_its_jobs_instead_of_removing_them(db, pg):
    user = billing.sign_in(db, ISSUER, "sub-mark", EMAIL, STARTER)
    billing.sign_in(db, ISSUER, "sub-mark-2", EMAIL, STARTER)
    done, _ = make_job(db, user)
    failed, _ = make_job(db, user, state="failed")
    pg.conn.execute(
        "UPDATE jobs SET dataset_removed_at = now(), dataset_purged_at = now() "
        "WHERE job_id = %s::uuid",
        (done,),
    )
    pg.conn.execute(
        "INSERT INTO sessions (session_hash, user_id, last_seen_at) VALUES (%s, %s, now())",
        ("a" * 64, user),
    )
    balance_before = pg.balance(user)
    ledger_before = pg.ledger(user)

    deleted = billing.delete_account(db, user)

    assert set(deleted) == {(ISSUER, "sub-mark"), (ISSUER, "sub-mark-2")}
    row = pg.one("SELECT * FROM users WHERE user_id = %s", user)
    assert row is not None, "delete_account removed the account instead of marking it"
    assert row["deleted_at"] is not None
    assert row["email"] == f"deleted:{user}"
    assert pg.balance(user) == balance_before
    assert pg.ledger(user) == ledger_before
    for job_id in (done, failed):
        job = pg.job(job_id)
        assert job is not None, "delete_account removed a job instead of marking it"
        assert job["deleted_at"] is not None
        assert job["filename"] is None
        assert job["dataset_purged_at"] is None
    assert pg.rows("SELECT * FROM sessions WHERE user_id = %s", user) == []
    assert pg.rows("SELECT * FROM identities WHERE user_id = %s", user) == []


def test_remove_deleted_account_needs_the_mark_and_a_finished_purge(aws, db, pg):
    unmarked = make_user(db, OTHER, email="other@example.com")
    assert billing.remove_deleted_account(db, unmarked) is False
    assert pg.one("SELECT * FROM users WHERE user_id = %s", unmarked) is not None

    user = make_user(db)
    job_id, key = make_job(db, user)
    seed_files(aws, job_id, key)
    billing.delete_account(db, user)

    assert billing.remove_deleted_account(db, user) is False
    assert pg.one("SELECT * FROM users WHERE user_id = %s", user) is not None
    assert pg.job(job_id) is not None

    billing.purge_job_files(db, aws.s3, aws.bucket, job_id)
    assert pg.job(job_id)["dataset_purged_at"] is not None

    assert billing.remove_deleted_account(db, user) is True
    assert pg.one("SELECT * FROM users WHERE user_id = %s", user) is None
    assert pg.balance(user) is None
    assert pg.rows("SELECT * FROM ledger WHERE user_id = %s", user) == []
    assert pg.rows("SELECT * FROM jobs WHERE user_id = %s", user) == []
    assert pg.one("SELECT * FROM users WHERE user_id = %s", unmarked) is not None


def test_reserve_refuses_a_deleted_account_and_creates_nothing(db, pg):
    user = make_user(db)
    billing.delete_account(db, user)
    job_id = str(uuid.uuid4())
    key = storage.object_key(user, job_id, "video/mp4")
    before = pg.snapshot()

    with pytest.raises(billing.AccountDeleted):
        billing.reserve(db, user, job_id, key, "clip.mp4", 3000)

    assert pg.job(job_id) is None
    assert pg.snapshot() == before


def test_presign_answers_not_signed_in_for_a_deleted_account(cognito_client, idp, pg):
    assert sign_in(cognito_client, idp, email=EMAIL).status_code in (302, 303)
    user_id = pg.one("SELECT user_id FROM users WHERE lower(email) = lower(%s)", EMAIL)["user_id"]
    pg.conn.execute("UPDATE users SET deleted_at = now() WHERE user_id = %s", (user_id,))

    resp = cognito_client.post(
        "/api/uploads/presign",
        json={
            "filename": "ad.mp4",
            "content_type": "video/mp4",
            "client_duration_seconds": 27.4,
            "research_consent": False,
        },
    )

    assert resp.status_code == 401
    assert resp.get_json()["error"] == "not_signed_in"
    assert pg.rows("SELECT * FROM jobs WHERE user_id = %s", user_id) == []


def test_signing_in_again_after_a_mark_gives_a_new_empty_account(db, pg):
    first = billing.sign_in(db, ISSUER, "sub-again", EMAIL, STARTER)
    billing.delete_account(db, first)
    assert pg.one("SELECT * FROM users WHERE user_id = %s", first)["deleted_at"] is not None

    second = billing.sign_in(db, ISSUER, "sub-again", EMAIL, STARTER)

    assert second != first
    assert pg.balance(second) == (STARTER, 0)
    assert pg.rows("SELECT * FROM jobs WHERE user_id = %s", second) == []
    assert pg.one("SELECT * FROM identities WHERE user_id = %s", second)["subject"] == "sub-again"


# ---------------------------------------------------------------- B. reward preview


def test_curve_reward_preview_is_zero_with_only_starter_credit(db, pg):
    user = make_user(db)
    job_id, _ = make_job(db, user, sha="a" * 64)

    assert preview(db, user, job_id)["curve_reward_cents"] == 0


def test_curve_reward_preview_matches_what_add_curve_pays(db, pg):
    user = make_user(db)
    billing.grant_credit(db, f"{user}@example.com", 500)
    job_id, _ = make_job(db, user, sha="b" * 64)

    shown = preview(db, user, job_id)["curve_reward_cents"]
    assert shown > 0
    paid = add_curve(db, user, job_id, "c" * 64)

    assert paid["rewarded_cents"] == shown


def test_curve_reward_preview_is_zero_at_the_reward_cap(db, pg):
    user = make_user(db)
    billing.grant_credit(db, f"{user}@example.com", 500)
    rewarded, _ = make_job(db, user, sha="d" * 64)
    assert add_curve(db, user, rewarded, "e" * 64)["rewarded_cents"] > 0

    next_job, _ = make_job(db, user, sha="f" * 64)

    assert preview(db, user, next_job, cap=1, window=30)["curve_reward_cents"] == 0


# ---------------------------------------------------------------- C. sharing needs done


def test_sharing_is_true_only_for_a_done_job(db, pg):
    user = make_user(db)
    done, _ = make_job(db, user)
    queued, _ = make_job(db, user, state="queued")
    failed, _ = make_job(db, user, state="failed")

    rows = {str(row["job_id"]): row for row in billing.list_jobs(db, user, 20)}

    assert rows[done]["sharing"] is True
    assert rows[queued]["sharing"] is False
    assert rows[failed]["sharing"] is False


# ---------------------------------------------------------------- D. stopped, then deleted


def test_deleting_a_stopped_copy_marks_its_own_files_for_purging_again(db, pg):
    user = make_user(db)
    job_id, _ = make_job(db, user)
    assert billing.mark_dataset_removed(db, job_id, "stopped", user_id=user) is True
    billing.mark_dataset_purged(db, job_id)
    assert pg.job(job_id)["dataset_purged_at"] is not None

    assert billing.delete_job(db, user, job_id) is True

    job = pg.job(job_id)
    assert job["deleted_at"] is not None
    assert job["dataset_purged_at"] is None


def test_deleting_a_stopped_copy_purges_its_own_files(aws, db, pg, app_for):
    user = WEB_USER_ID
    make_user(db, user, email=f"{user}@example.com")
    job_id, key = make_job(db, user)
    research, own = seed_files(aws, job_id, key)
    client = app_for()

    assert client.post(f"/api/jobs/{job_id}/stop-sharing").status_code == 200
    assert not any(object_exists(aws, name) for name in research)
    assert all(object_exists(aws, name) for name in own)

    resp = client.delete(f"/api/jobs/{job_id}")

    assert resp.status_code == 200
    assert all(not object_exists(aws, name) for name in own)
    assert pg.job(job_id)["dataset_purged_at"] is not None


def test_the_reaper_finishes_a_stopped_and_deleted_copys_own_files(
    aws, db, pg, reaper_env, app_for
):
    reaper_env(90)
    user = WEB_USER_ID
    make_user(db, user, email=f"{user}@example.com")
    job_id, key = make_job(db, user)
    research, own = seed_files(aws, job_id, key)
    client = app_for()

    assert client.post(f"/api/jobs/{job_id}/stop-sharing").status_code == 200
    failing = app_for(s3_client=DeleteReportsErrors(aws.s3))
    resp = failing.delete(f"/api/jobs/{job_id}")

    assert resp.status_code == 200
    job = pg.job(job_id)
    assert job["deleted_at"] is not None
    assert job["dataset_purged_at"] is None
    assert all(object_exists(aws, name) for name in own)

    run_reaper()

    job = pg.job(job_id)
    assert job["dataset_purged_at"] is not None
    assert all(not object_exists(aws, name) for name in own)


# ---------------------------------------------------------------- E. snapshots and the reward guard


def test_purging_a_job_deletes_its_retention_snapshots(aws, db, pg):
    user = make_user(db)
    job_id, key = make_job(db, user, sha="1" * 64)
    seed_files(aws, job_id, key)
    assert add_curve(db, user, job_id, "2" * 64)["rewarded_cents"] == 0
    assert len(snapshot_rows(pg, job_id)) == 1

    assert billing.delete_job(db, user, job_id) is True
    billing.purge_job_files(db, aws.s3, aws.bucket, job_id)

    assert snapshot_rows(pg, job_id) == []


def test_the_reaper_deletes_snapshots_older_than_1095_days(aws, db, pg, reaper_env):
    reaper_env(90)
    user = make_user(db)
    job_id, _ = make_job(db, user)
    old = str(uuid.uuid4())
    recent = str(uuid.uuid4())
    pg.conn.execute(
        "INSERT INTO retention_snapshots (snapshot_id, job_id, added_at, curve_sha256) "
        "VALUES (%s::uuid, %s::uuid, now() - make_interval(days => 1096), %s), "
        "(%s::uuid, %s::uuid, now() - make_interval(days => 1094), %s)",
        (old, job_id, "3" * 64, recent, job_id, "4" * 64),
    )

    run_reaper()

    remaining = {str(row["snapshot_id"]) for row in snapshot_rows(pg, job_id)}
    assert old not in remaining
    assert recent in remaining


def test_a_purged_stopped_videos_hash_still_blocks_another_reward(aws, db, pg):
    user = make_user(db)
    billing.grant_credit(db, f"{user}@example.com", 500)
    video = "5" * 64
    first, _ = make_job(db, user, sha=video)
    assert add_curve(db, user, first, "6" * 64)["rewarded_cents"] > 0
    assert billing.mark_dataset_removed(db, first, "stopped", user_id=user) is True
    billing.purge_job_files(db, aws.s3, aws.bucket, first)

    second, _ = make_job(db, user, sha=video)

    assert add_curve(db, user, second, "7" * 64)["rewarded_cents"] == 0


# ---------------------------------------------------------------- F. retention route writes


def test_a_failed_second_retention_write_deletes_the_first_and_records_nothing(
    aws, db, pg, app_for
):
    user = WEB_USER_ID
    make_user(db, user, email=f"{user}@example.com")
    job_id, _ = make_job(db, user)
    s3 = SecondPutFails(aws.s3)
    client = app_for(s3_client=s3)

    resp = client.post(
        f"/api/jobs/{job_id}/retention",
        json={
            "report": report_text(),
            "views": VIEWS,
            "published": PUBLISHED,
            "promoted": False,
        },
    )

    assert resp.status_code == 500
    assert snapshot_rows(pg, job_id) == []
    assert prefix_keys(aws, f"dataset/{job_id}/retention/") == []


# ---------------------------------------------------------------- G. /app renews the hint


def cookie(resp, name):
    for line in resp.headers.getlist("Set-Cookie"):
        if line.startswith(f"{name}="):
            return line
    return None


def cookie_lifetime(line):
    parts = {part.split("=", 1)[0].strip().lower(): part for part in line.split(";")[1:]}
    if "max-age" in parts:
        return int(parts["max-age"].split("=", 1)[1])
    expires = parsedate_to_datetime(parts["expires"].split("=", 1)[1])
    return expires.timestamp() - time.time()


def test_app_sets_the_signed_in_hint_for_its_full_lifetime(cognito_client, idp):
    assert sign_in(cognito_client, idp).status_code in (302, 303)

    resp = cognito_client.get("/app")

    assert resp.status_code == 200
    line = cookie(resp, HINT_COOKIE)
    assert line is not None, "/app did not renew the signed-in hint cookie"
    assert abs(cookie_lifetime(line) - billing.SESSION_DAYS * DAY) < 120


# ---------------------------------------------------------------- H. app.py loads .env first


REFUSAL = (
    "python app.py runs only against the local fake stack: python infra/local_stack.py. "
    "The real .env is for the admin scripts."
)


def app_root(tmp_path):
    root = tmp_path / "root"
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
    return root


def test_app_py_refuses_a_remote_service_endpoint_set_only_in_dotenv(tmp_path):
    root = app_root(tmp_path)
    (root / ".env").write_text("AWS_ENDPOINT_URL_S3=https://s3.us-east-1.amazonaws.com\n")
    env = os.environ.copy()
    for name in list(env):
        if name.startswith("AWS_ENDPOINT_URL") or name == "AWS_IGNORE_CONFIGURED_ENDPOINT_URLS":
            env.pop(name, None)
    env["NEUROLENS_ROOT"] = str(root)
    env["AWS_ENDPOINT_URL"] = "http://127.0.0.1:5005"

    try:
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "app.py")],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("app.py started even though .env points a service endpoint away from localhost")

    assert result.returncode != 0
    assert REFUSAL in result.stdout + result.stderr


# ---------------------------------------------------------------- I. local stack retries


def load_local_stack():
    path = Path(__file__).resolve().parents[1] / "infra" / "local_stack.py"
    spec = importlib.util.spec_from_file_location("local_stack", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StopLoop(Exception):
    """Ends the local stack's endless dispatch loop after the message under test."""


class OneMessageQueue:
    def __init__(self, body):
        self.body = body
        self.deleted = []
        self.receives = 0

    def receive_message(self, **kwargs):
        self.receives += 1
        if self.receives > 1:
            raise StopLoop
        return {"Messages": [{"Body": self.body, "ReceiptHandle": "receipt-1"}]}

    def delete_message(self, **kwargs):
        self.deleted.append(kwargs)


def test_local_stack_leaves_a_dispatch_error_on_the_queue(monkeypatch):
    local_stack = load_local_stack()
    from neurolens.lambdas import dispatcher

    def fail(*args, **kwargs):
        raise RuntimeError("dispatch failed")

    monkeypatch.setattr(dispatcher, "dispatch", fail)
    event = {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "neurolens-local"},
                    "object": {"key": "uploads/u/1234.mp4"},
                }
            }
        ]
    }
    sqs = OneMessageQueue(json.dumps(event))

    with pytest.raises(StopLoop):
        local_stack._dispatch_loop(
            sqs, "queue-url", None, None, None, {"max_upload_bytes": 300_000_000}
        )

    assert sqs.deleted == [], "the local stack deleted a message whose dispatch failed"


# ---------------------------------------------------------------- J. stale purge and sign-in races


def test_a_purge_racing_a_job_delete_does_not_mark_the_own_files_purged(
    aws, db, pg, monkeypatch, reaper_env
):
    reaper_env(90)
    user = make_user(db)
    job_id, key = make_job(db, user)
    research, own = seed_files(aws, job_id, key)
    assert billing.mark_dataset_removed(db, job_id, "stopped") is True
    real = storage.delete_job_prefixes

    def delete_after_mark(s3, bucket, job):
        assert billing.delete_job(db, user, job_id) is True
        return real(s3, bucket, job)

    monkeypatch.setattr(storage, "delete_job_prefixes", delete_after_mark)

    billing.purge_job_files(db, aws.s3, aws.bucket, job_id)

    job = pg.job(job_id)
    assert job["deleted_at"] is not None
    assert job["dataset_purged_at"] is None, (
        "purge marked the copy purged although the job was deleted after it read the row"
    )
    assert not any(object_exists(aws, name) for name in research)
    assert all(object_exists(aws, name) for name in own)

    monkeypatch.setattr(storage, "delete_job_prefixes", real)
    run_reaper()

    job = pg.job(job_id)
    assert job["dataset_purged_at"] is not None
    assert all(not object_exists(aws, name) for name in own)


def test_signing_in_after_a_delete_with_a_new_identity_creates_a_new_user(db, pg):
    user = billing.sign_in(db, ISSUER, "sub-after-delete-new", EMAIL, STARTER)
    billing.delete_account(db, user)

    new_user = billing.sign_in(db, ISSUER, "sub-after-delete-new-2", EMAIL, STARTER)

    assert new_user != user
    assert pg.one("SELECT * FROM users WHERE user_id = %s", new_user)["deleted_at"] is None
    assert pg.balance(new_user) == (STARTER, 0)
    assert pg.one("SELECT * FROM users WHERE user_id = %s", new_user)["email"] == EMAIL
    assert pg.one("SELECT * FROM users WHERE user_id = %s", user)["email"] == f"deleted:{user}"
    assert pg.rows("SELECT * FROM identities WHERE user_id = %s", user) == []
    assert billing.remove_deleted_account(db, user) is True
    assert pg.one("SELECT * FROM users WHERE user_id = %s", user) is None


def test_signing_in_after_a_delete_with_the_old_identity_creates_a_new_user(db, pg):
    user = billing.sign_in(db, ISSUER, "sub-after-delete-old", EMAIL, STARTER)
    billing.delete_account(db, user)

    new_user = billing.sign_in(db, ISSUER, "sub-after-delete-old", EMAIL, STARTER)

    assert new_user != user
    assert pg.one("SELECT * FROM users WHERE user_id = %s", new_user)["deleted_at"] is None
    assert pg.one("SELECT * FROM users WHERE user_id = %s", user)["email"] == f"deleted:{user}"
    assert pg.rows("SELECT * FROM identities WHERE user_id = %s", user) == []
    assert pg.one("SELECT * FROM identities WHERE user_id = %s", new_user)["subject"] == (
        "sub-after-delete-old"
    )
    assert billing.remove_deleted_account(db, user) is True
    assert pg.one("SELECT * FROM users WHERE user_id = %s", user) is None


def test_a_sign_in_racing_a_delete_account_leaves_no_identity_on_the_marked_user(db, pg, row_lock):
    user = billing.sign_in(db, ISSUER, "sub-sign-in-race", EMAIL, STARTER)
    lock = row_lock()
    lock.execute("SELECT 1 FROM users WHERE user_id = %s FOR UPDATE", user)
    signed, sign_errors, delete_errors = [], [], []

    def do_sign_in():
        try:
            signed.append(billing.sign_in(db, ISSUER, "sub-sign-in-race-new", EMAIL, STARTER))
        except Exception as err:  # reported below
            sign_errors.append(err)

    def do_delete():
        try:
            billing.delete_account(db, user)
        except Exception as err:  # reported below
            delete_errors.append(err)

    threads = [threading.Thread(target=do_sign_in), threading.Thread(target=do_delete)]
    for thread in threads:
        thread.start()
    lock.wait_for_blocked(2, timeout=5)
    lock.release()
    for thread in threads:
        thread.join(20)
    assert not any(thread.is_alive() for thread in threads)
    assert not sign_errors, sign_errors
    assert not delete_errors, delete_errors
    assert len(signed) == 1
    assert pg.one("SELECT * FROM users WHERE user_id = %s", user)["deleted_at"] is not None

    new_user = signed[0]
    if new_user == user:
        assert (
            pg.rows(
                "SELECT * FROM identities WHERE issuer = %s AND subject = %s",
                ISSUER,
                "sub-sign-in-race-new",
            )
            == []
        ), "a sign-in racing the delete left an identity on the marked account"
    else:
        assert pg.one("SELECT * FROM users WHERE user_id = %s", new_user)["deleted_at"] is None
    assert pg.rows("SELECT * FROM identities WHERE user_id = %s", user) == []
    assert billing.remove_deleted_account(db, user) is True
    assert pg.one("SELECT * FROM users WHERE user_id = %s", user) is None
