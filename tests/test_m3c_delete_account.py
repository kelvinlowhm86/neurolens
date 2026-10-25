"""M3c account deletion (docs/M3c_spec.md §5, §8).

`billing.delete_account` (rows gone, other users intact, refused while active, the lock against a
racing `reserve`) and `POST /api/account/delete` (confirmation, files and rows, one Cognito delete
per Cognito identity, a missing user counts as done, failure logging, and the stale-session 401).
"""

import json
import logging
import re
import threading
import uuid
from pathlib import PurePosixPath

import pytest
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from conftest import ISSUER, STARTER_CENTS, check_invariants, sign_in
from neurolens import billing, storage
from neurolens.web.app import create_app
from test_m3c_delete_job import DeleteReportsError
from test_m3f_purge_retry import run_reaper

GOOGLE = "https://accounts.google.example"
EMAIL = "ann@example.com"


@pytest.fixture
def dev_client(aws, db, make_cfg, tmp_path):
    app = create_app(cfg=make_cfg(), data_dir=tmp_path / "data", db=db, s3_client=aws.s3)
    app.config["TESTING"] = True
    return app.test_client()


class FakeCognito:
    """What a Cognito admin client returns; records every ListUsers filter and deleted username."""

    def __init__(self):
        self.filters = []
        self.deleted = []
        self.users_empty = False
        self.fail_on = None
        self.fail_operation = None
        self.fail_subject = None


@pytest.fixture
def cognito_admin(monkeypatch):
    fake = FakeCognito()
    real = BaseClient._make_api_call

    def make_api_call(self, operation_name, api_params):
        if operation_name == "ListUsers":
            filt = api_params.get("Filter", "")
            match = re.search(r'sub\s*=\s*"([^"]*)"', filt)
            subject = match.group(1) if match else None
            if fake.fail_on == "ListUsers" or (
                fake.fail_operation == "ListUsers" and subject == fake.fail_subject
            ):
                raise ClientError(
                    {"Error": {"Code": "InternalError", "Message": "boom"}}, operation_name
                )
            fake.filters.append(filt)
            if fake.users_empty:
                return {"Users": []}
            if match is None:
                raise AssertionError(f"ListUsers Filter does not name a sub: {filt!r}")
            return {"Users": [{"Username": f"user-{subject}"}]}
        if operation_name == "AdminDeleteUser":
            username = api_params.get("Username")
            if fake.fail_on == "AdminDeleteUser" or (
                fake.fail_operation == "AdminDeleteUser" and username == f"user-{fake.fail_subject}"
            ):
                raise ClientError(
                    {"Error": {"Code": "InternalError", "Message": "boom"}}, operation_name
                )
            fake.deleted.append(username)
            return {}
        return real(self, operation_name, api_params)

    monkeypatch.setattr(BaseClient, "_make_api_call", make_api_call)
    return fake


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


def delete_account(client, **body):
    return client.post("/api/account/delete", json=body)


def add_identity(db, issuer, subject, email=EMAIL):
    return billing.sign_in(db, issuer, subject, email, 0)


def signed_in_user_id(client, idp, pg, *, email=EMAIL, sub="google_1001"):
    assert sign_in(client, idp, email=email, sub=sub).status_code in (302, 303)
    return pg.one("SELECT user_id FROM users WHERE lower(email) = lower(%s)", email)["user_id"]


def reserve_job(db, user_id, *, content_type="video/mp4", done=True):
    job_id = str(uuid.uuid4())
    key = storage.object_key(user_id, job_id, content_type)
    ext = PurePosixPath(key).suffix
    billing.reserve(db, user_id, job_id, key, f"clip{ext}", 3000)
    if done:
        assert billing.settle_success(db, job_id) is True
    return job_id, key


def seed_files(aws, job_id, key):
    ext = PurePosixPath(key).suffix
    dataset_video = f"dataset/{job_id}/video{ext}"
    for name, body in (
        (key, b"video"),
        (f"results/{job_id}.json", b"{}"),
        (dataset_video, b"video"),
        (f"dataset/{job_id}/result.json", b"{}"),
        (f"dataset/{job_id}/retention.csv", b"elapsed_ratio,watch_ratio\n0,0\n1,1\n"),
    ):
        aws.s3.put_object(Bucket=aws.bucket, Key=name, Body=body)
    return [
        key,
        f"results/{job_id}.json",
        dataset_video,
        f"dataset/{job_id}/result.json",
        f"dataset/{job_id}/retention.csv",
    ]


def object_exists(aws, key):
    try:
        aws.s3.head_object(Bucket=aws.bucket, Key=key)
    except ClientError as err:
        assert err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound")
        return False
    return True


class DeleteFailsThenWorks(DeleteReportsError):
    """DeleteReportsError while `failing`, then the real client once it works again."""

    failing = True

    def delete_objects(self, **kwargs):
        if self.failing:
            return super().delete_objects(**kwargs)
        return self._inner.delete_objects(**kwargs)


# ---------------------------------------------------------------- billing.delete_account


def test_delete_account_marks_the_rows_and_keeps_other_users_intact(db, pg):
    target = billing.sign_in(db, ISSUER, "sub-t", "target@example.com", STARTER_CENTS)
    add_identity(db, ISSUER, "sub-t2", "target@example.com")
    other = billing.sign_in(db, ISSUER, "sub-o", "other@example.com", STARTER_CENTS)
    done, _ = reserve_job(db, target)
    failed, _ = reserve_job(db, target, done=False)
    assert billing.issue_refund(db, failed, "unreadable_video", queued=True) is True
    # A copy purged earlier (Stop sharing, the 90-day rule) must get its own files purged too.
    pg.conn.execute(
        "UPDATE jobs SET dataset_removed_at = now(), dataset_purged_at = now() "
        "WHERE job_id = %s::uuid",
        (done,),
    )
    other_job, _ = reserve_job(db, other)
    balance_before = pg.balance(target)
    ledger_before = pg.ledger(target)
    check_invariants(pg.conn)

    deleted = billing.delete_account(db, target)

    assert set(deleted) == {(ISSUER, "sub-t"), (ISSUER, "sub-t2")}
    row = pg.one("SELECT * FROM users WHERE user_id = %s", target)
    assert row is not None, "delete_account removed the account instead of marking it"
    assert row["deleted_at"] is not None
    assert row["email"] == f"deleted:{target}"
    assert pg.balance(target) == balance_before
    assert pg.ledger(target) == ledger_before
    for job_id in (done, failed):
        job = pg.job(job_id)
        assert job is not None, "delete_account removed a job instead of marking it"
        assert job["deleted_at"] is not None
        assert job["filename"] is None
        assert job["dataset_purged_at"] is None
    assert pg.rows("SELECT * FROM identities WHERE user_id = %s", target) == []
    assert pg.rows("SELECT * FROM sessions WHERE user_id = %s", target) == []
    assert pg.one("SELECT * FROM users WHERE user_id = %s", other) is not None
    assert pg.balance(other) is not None
    other_jobs = pg.rows("SELECT job_id::text AS job_id FROM jobs WHERE user_id = %s", other)
    assert {job["job_id"] for job in other_jobs} == {other_job}
    assert any(row["job_id"] == other_job for row in pg.ledger(other))
    check_invariants(pg.conn)


@pytest.mark.parametrize("processing", [False, True], ids=["queued", "processing"])
def test_delete_account_refuses_while_a_job_is_active(db, pg, processing):
    user = billing.sign_in(db, ISSUER, "sub-a", "active@example.com", STARTER_CENTS)
    job_id, _ = reserve_job(db, user, done=False)
    if processing:
        assert billing.claim(db, job_id) == 1
    before = pg.snapshot()
    with pytest.raises(billing.JobActive):
        billing.delete_account(db, user)
    assert pg.snapshot() == before


def test_a_reserve_racing_delete_account_cannot_leave_a_job_behind(db, pg, row_lock):
    user = billing.sign_in(db, ISSUER, "sub-race", "race@example.com", STARTER_CENTS)
    job_id = str(uuid.uuid4())
    key = storage.object_key(user, job_id, "video/mp4")
    lock = row_lock()
    lock.execute("SELECT 1 FROM balances WHERE user_id = %s FOR UPDATE", user)
    reserved, reserve_errors, delete_errors = [], [], []

    def do_reserve():
        try:
            reserved.append(billing.reserve(db, user, job_id, key, "clip.mp4", 3000))
        except Exception as err:  # reported below
            reserve_errors.append(err)

    def do_delete():
        try:
            billing.delete_account(db, user)
        except Exception as err:  # reported below
            delete_errors.append(err)

    threads = [threading.Thread(target=do_reserve), threading.Thread(target=do_delete)]
    for thread in threads:
        thread.start()
    lock.wait_for_blocked(2)
    lock.release()
    for thread in threads:
        thread.join(20)
    assert not any(thread.is_alive() for thread in threads)

    job = pg.job(job_id)
    user_row = pg.one("SELECT * FROM users WHERE user_id = %s", user)
    assert user_row is not None, "a delete removed the account instead of marking it"
    if reserved:
        assert delete_errors and isinstance(delete_errors[0], billing.JobActive)
        assert job is not None
        assert user_row["deleted_at"] is None
    else:
        assert delete_errors == []
        assert reserve_errors and isinstance(reserve_errors[0], billing.AccountDeleted)
        assert job is None
        assert user_row["deleted_at"] is not None


# ---------------------------------------------------------------- the route


def test_account_delete_works_in_cognito_mode_but_is_404_in_dev_mode(
    dev_client, cognito_client, idp, pg, cognito_admin
):
    # SPEC-AMBIGUITY: "404 in dev mode" may be a registered route that refuses or no route at
    # all; only the answer is asserted.
    assert delete_account(dev_client, confirm="DELETE").status_code == 404
    user_id = signed_in_user_id(cognito_client, idp, pg)
    assert delete_account(cognito_client, confirm="DELETE").status_code == 200
    assert pg.one("SELECT * FROM users WHERE user_id = %s", user_id) is None


def test_account_delete_requires_the_exact_confirmation(cognito_client, idp, pg):
    user_id = signed_in_user_id(cognito_client, idp, pg)
    for body in ({}, {"confirm": "delete"}, {"confirm": "DELETE "}, {"confirm": True}):
        resp = delete_account(cognito_client, **body)
        assert resp.status_code == 400, body
        assert resp.get_json()["error"] == "confirmation_required", body
    assert pg.one("SELECT * FROM users WHERE user_id = %s", user_id) is not None


@pytest.mark.parametrize("content_type", ["video/mp4", "video/quicktime"], ids=["mp4", "mov"])
def test_account_delete_removes_the_users_files_and_rows(
    cognito_client, idp, aws, db, pg, cognito_admin, content_type
):
    user_id = signed_in_user_id(cognito_client, idp, pg)
    job_id, key = reserve_job(db, user_id, content_type=content_type)
    keys = seed_files(aws, job_id, key)
    other = billing.sign_in(db, ISSUER, "sub-other", "other@example.com", STARTER_CENTS)
    other_job, other_key = reserve_job(db, other)
    aws.s3.put_object(Bucket=aws.bucket, Key=other_key, Body=b"other video")

    resp = delete_account(cognito_client, confirm="DELETE")

    assert resp.status_code == 200
    assert pg.one("SELECT * FROM users WHERE user_id = %s", user_id) is None
    assert pg.balance(user_id) is None
    assert pg.rows("SELECT * FROM jobs WHERE user_id = %s", user_id) == []
    assert pg.rows("SELECT * FROM ledger WHERE user_id = %s", user_id) == []
    assert pg.rows("SELECT * FROM identities WHERE user_id = %s", user_id) == []
    assert not any(object_exists(aws, name) for name in keys)
    assert object_exists(aws, other_key)  # another user's file is untouched
    assert pg.one("SELECT * FROM users WHERE user_id = %s", other) is not None
    assert pg.job(other_job) is not None
    # §5 step 4: the route clears the same client's session.
    assert cognito_client.get("/api/me").status_code == 401


@pytest.mark.parametrize("content_type", ["video/mp4", "video/quicktime"], ids=["mp4", "mov"])
def test_a_reported_s3_error_still_answers_200_and_the_reaper_finishes_the_delete(
    cognito_app, idp, aws, db, pg, cognito_admin, content_type, reaper_env
):
    reaper_env(90)
    s3 = DeleteFailsThenWorks(aws.s3)
    client = cognito_app(s3_client=s3).test_client()
    user_id = signed_in_user_id(client, idp, pg, sub="google_1001")
    add_identity(db, ISSUER, "email_2002", EMAIL)
    job_id, key = reserve_job(db, user_id, content_type=content_type)
    keys = seed_files(aws, job_id, key)

    resp = delete_account(client, confirm="DELETE")

    assert resp.status_code == 200
    assert resp.get_json() == {"deleted": True}
    user_row = pg.one("SELECT * FROM users WHERE user_id = %s", user_id)
    assert user_row is not None and user_row["deleted_at"] is not None
    assert user_row["email"] == f"deleted:{user_id}"
    job = pg.job(job_id)
    assert job is not None and job["deleted_at"] is not None
    assert job["dataset_purged_at"] is None
    assert pg.rows("SELECT * FROM identities WHERE user_id = %s", user_id) == []
    assert all(object_exists(aws, name) for name in keys)
    assert sorted(cognito_admin.deleted) == ["user-email_2002", "user-google_1001"]
    assert client.get("/api/me").status_code == 401

    s3.failing = False
    run_reaper()

    assert pg.one("SELECT * FROM users WHERE user_id = %s", user_id) is None
    assert pg.balance(user_id) is None
    assert pg.rows("SELECT * FROM ledger WHERE user_id = %s", user_id) == []
    assert pg.rows("SELECT * FROM jobs WHERE user_id = %s", user_id) == []
    assert not any(object_exists(aws, name) for name in keys)


def test_cognito_is_called_once_per_cognito_identity(cognito_client, idp, db, pg, cognito_admin):
    user_id = signed_in_user_id(cognito_client, idp, pg, sub="google_1001")
    add_identity(db, ISSUER, "email_2002", EMAIL)
    add_identity(db, GOOGLE, "google_other", EMAIL)
    assert {
        row["subject"]
        for row in pg.rows("SELECT subject FROM identities WHERE user_id = %s", user_id)
    } == {"google_1001", "email_2002", "google_other"}

    resp = delete_account(cognito_client, confirm="DELETE")

    assert resp.status_code == 200
    assert len(cognito_admin.filters) == 2
    for subject in ("google_1001", "email_2002"):
        assert any(subject in filt for filt in cognito_admin.filters)
    assert not any("google_other" in filt for filt in cognito_admin.filters)
    assert sorted(cognito_admin.deleted) == ["user-email_2002", "user-google_1001"]
    assert pg.one("SELECT * FROM users WHERE user_id = %s", user_id) is None


def test_list_users_finding_nothing_counts_as_done(cognito_client, idp, db, pg, cognito_admin):
    user_id = signed_in_user_id(cognito_client, idp, pg)
    cognito_admin.users_empty = True
    resp = delete_account(cognito_client, confirm="DELETE")
    assert resp.status_code == 200
    assert cognito_admin.deleted == []
    assert pg.one("SELECT * FROM users WHERE user_id = %s", user_id) is None


def test_a_cognito_failure_is_500_login_delete_failed_and_logs_the_sub(
    cognito_client, idp, db, pg, cognito_admin, caplog
):
    user_id = signed_in_user_id(cognito_client, idp, pg, sub="google_1001")
    cognito_admin.fail_on = "AdminDeleteUser"
    with caplog.at_level(logging.DEBUG):
        resp = delete_account(cognito_client, confirm="DELETE")
    assert resp.status_code == 500
    assert resp.get_json()["error"] == "login_delete_failed"
    assert pg.one("SELECT * FROM users WHERE user_id = %s", user_id) is None
    marker = [r.getMessage() for r in caplog.records if "COGNITO_DELETE_FAILED" in r.getMessage()]
    assert marker, "no COGNITO_DELETE_FAILED log line"
    assert any("google_1001" in line for line in marker)


def test_a_list_users_failure_is_500_login_delete_failed_and_logs_the_sub(
    cognito_client, idp, db, pg, cognito_admin, caplog
):
    user_id = signed_in_user_id(cognito_client, idp, pg, sub="google_1001")
    cognito_admin.fail_on = "ListUsers"
    with caplog.at_level(logging.DEBUG):
        resp = delete_account(cognito_client, confirm="DELETE")
    assert resp.status_code == 500
    assert resp.get_json()["error"] == "login_delete_failed"
    assert pg.one("SELECT * FROM users WHERE user_id = %s", user_id) is None
    marker = [r.getMessage() for r in caplog.records if "COGNITO_DELETE_FAILED" in r.getMessage()]
    assert marker, "no COGNITO_DELETE_FAILED log line"
    assert any("google_1001" in line for line in marker)


@pytest.mark.parametrize("operation", ["ListUsers", "AdminDeleteUser"])
def test_a_failed_login_does_not_stop_the_other_cognito_login_being_deleted(
    cognito_client, idp, db, pg, cognito_admin, caplog, operation
):
    user_id = signed_in_user_id(cognito_client, idp, pg, sub="google_1001")
    add_identity(db, ISSUER, "email_2002", EMAIL)
    cognito_admin.fail_operation = operation
    cognito_admin.fail_subject = "google_1001"
    with caplog.at_level(logging.DEBUG):
        resp = delete_account(cognito_client, confirm="DELETE")
    assert resp.status_code == 500
    assert resp.get_json()["error"] == "login_delete_failed"
    assert pg.one("SELECT * FROM users WHERE user_id = %s", user_id) is None
    assert cognito_admin.deleted == ["user-email_2002"]
    marker = [r.getMessage() for r in caplog.records if "COGNITO_DELETE_FAILED" in r.getMessage()]
    assert len(marker) == 1, marker
    assert "google_1001" in marker[0]
    assert cognito_client.get("/api/me").status_code == 401


def test_a_stale_session_after_deletion_is_401_not_500(cognito_client, idp, db, pg):
    user_id = signed_in_user_id(cognito_client, idp, pg)
    assert cognito_client.get("/api/me").status_code == 200
    billing.delete_account(db, user_id)
    resp = cognito_client.get("/api/me")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "not_signed_in"
    assert cognito_client.get("/api/jobs").status_code == 401
    with cognito_client.session_transaction() as sess:
        assert "user_id" not in sess


def test_account_delete_is_409_while_a_job_is_active(cognito_client, idp, db, pg):
    user_id = signed_in_user_id(cognito_client, idp, pg)
    job_id, _ = reserve_job(db, user_id, done=False)
    resp = delete_account(cognito_client, confirm="DELETE")
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "job_active"
    assert pg.one("SELECT * FROM users WHERE user_id = %s", user_id) is not None
    assert pg.job(job_id) is not None


# ---------------------------------------------------------------- payment removal


def test_me_no_longer_contains_can_top_up(dev_client):
    resp = dev_client.get("/api/me")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "can_top_up" not in data
    assert {"user_id", "email", "balance"} <= set(data)
