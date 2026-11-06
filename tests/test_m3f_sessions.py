"""M3f sign-in sessions (docs/M3f_spec.md §6b, §10 "Sessions").

A verified sign-in creates a random token, stores only its SHA-256 in `sessions` and puts only
the token in the signed cookie. A request is signed in while that row exists and `last_seen_at`
is within 30 days; use moves `last_seen_at` and the cookie's lifetime at most once an hour.
Sign-out and account deletion delete the row(s), the reaper deletes expired rows, and the landing
hint lasts as long as the session. Identity always comes from the database, never the cookie.
"""

import hashlib
import json
import secrets
import time
from email.utils import parsedate_to_datetime

import pytest
from botocore.client import BaseClient
from conftest import sign_in
from neurolens import billing

SESSION_DAYS = 30
DAY = 24 * 60 * 60
HINT_COOKIE = "nl_signed_in"


def session_of(client):
    """The signed cookie's own keys (Flask's `_permanent` marker aside)."""
    with client.session_transaction() as sess:
        return {k: v for k, v in sess.items() if k != "_permanent"}


def token_of(client):
    """The cookie's one value: the session token, with no identity beside it."""
    sess = session_of(client)
    assert "user_id" not in sess
    assert "email" not in sess
    assert len(sess) == 1, sess
    [(key, token)] = sess.items()
    assert isinstance(token, str) and token
    return key, token


def sha256(token):
    return hashlib.sha256(token.encode()).hexdigest()


def sessions(pg, user_id=None):
    if user_id is None:
        return pg.rows("SELECT * FROM sessions ORDER BY created_at")
    return pg.rows("SELECT * FROM sessions WHERE user_id = %s ORDER BY created_at", user_id)


def age_session(pg, token, seconds):
    pg.conn.execute(
        "UPDATE sessions SET last_seen_at = now() - make_interval(secs => %s) "
        "WHERE session_hash = %s",
        (seconds, sha256(token)),
    )


def cookie(resp, name):
    for line in resp.headers.getlist("Set-Cookie"):
        if line.startswith(f"{name}="):
            return line
    return None


def cookie_flags(line):
    _, *parts = [part.strip() for part in line.split(";")]
    return {part.split("=")[0].lower(): part for part in parts}


def lifetime(line):
    """Seconds a Set-Cookie lasts: Max-Age when present, else its Expires date."""
    flags = cookie_flags(line)
    if "max-age" in flags:
        return int(flags["max-age"].split("=", 1)[1])
    expires = parsedate_to_datetime(flags["expires"].split("=", 1)[1])
    return expires.timestamp() - time.time()


def session_cookie(resp):
    """The signed session's Set-Cookie line (the landing hint is a different cookie)."""
    lines = [
        line
        for line in resp.headers.getlist("Set-Cookie")
        if not line.startswith(f"{HINT_COOKIE}=")
    ]
    assert len(lines) == 1, lines
    return lines[0]


def user_id_for(pg, email="ann@example.com"):
    return pg.one("SELECT user_id FROM users WHERE lower(email) = lower(%s)", email)["user_id"]


# ---------------------------------------------------------------- the 006 migration


def test_migration_adds_the_sessions_table(pg):
    columns = {
        row["column_name"]: row
        for row in pg.rows(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'sessions'"
        )
    }
    assert {"session_hash", "user_id", "created_at", "last_seen_at"} <= set(columns)
    assert columns["session_hash"]["data_type"] == "text"
    assert columns["user_id"]["data_type"] == "text"
    for name in ("session_hash", "user_id", "last_seen_at"):
        assert columns[name]["is_nullable"] == "NO", name
    [primary_key] = pg.rows(
        "SELECT kcu.column_name FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
        "WHERE tc.table_schema = current_schema() AND tc.table_name = 'sessions' "
        "AND tc.constraint_type = 'PRIMARY KEY'"
    )
    assert primary_key["column_name"] == "session_hash"
    assert any(
        "user_id" in row["indexdef"]
        for row in pg.rows(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = current_schema() AND tablename = 'sessions'"
        )
    )


def test_the_session_lifetime_is_30_days_and_the_12_hour_rule_is_gone():
    from neurolens.web import auth

    assert auth.SESSION_DAYS == SESSION_DAYS
    assert not hasattr(auth, "SESSION_HOURS")


# ---------------------------------------------------------------- the cookie and the row


def test_a_verified_sign_in_stores_a_hashed_random_token_and_the_cookie_holds_only_it(
    cognito_app, idp, pg
):
    app = cognito_app()
    first, second = app.test_client(), app.test_client()
    assert sign_in(first, idp).status_code in (302, 303)
    assert sign_in(second, idp).status_code in (302, 303)

    _, token = token_of(first)
    _, other = token_of(second)
    assert token != other  # 32 random bytes each time
    hashes = {sha256(token), sha256(other)}
    rows = sessions(pg)
    assert len(rows) == 2
    assert {row["session_hash"] for row in rows} == hashes
    assert token not in {row["session_hash"] for row in rows}  # only the hash is stored
    assert {row["user_id"] for row in rows} == {user_id_for(pg)}
    assert all(row["last_seen_at"] is not None for row in rows)
    assert all(row["created_at"] is not None for row in rows)


def test_the_cookie_lifetime_is_30_days(cognito_client, idp):
    resp = sign_in(cognito_client, idp)

    assert abs(lifetime(session_cookie(resp)) - SESSION_DAYS * DAY) < 120


def test_the_landing_hint_follows_the_session_lifetime(cognito_client, idp):
    resp = sign_in(cognito_client, idp)

    line = cookie(resp, HINT_COOKIE)
    assert line is not None
    assert abs(lifetime(line) - SESSION_DAYS * DAY) < 120


# ---------------------------------------------------------------- the 30-day window


def test_a_row_seen_just_inside_30_days_works_and_just_outside_is_refused(cognito_client, idp, pg):
    sign_in(cognito_client, idp)
    _, token = token_of(cognito_client)

    age_session(pg, token, SESSION_DAYS * DAY - 60)
    assert cognito_client.get("/api/me").status_code == 200

    age_session(pg, token, SESSION_DAYS * DAY + 60)
    resp = cognito_client.get("/api/me")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "not_signed_in"


def test_last_seen_moves_with_use_and_slides_the_window_forward(cognito_client, idp, pg):
    sign_in(cognito_client, idp)
    _, token = token_of(cognito_client)

    age_session(pg, token, SESSION_DAYS * DAY - 60)
    assert cognito_client.get("/api/me").status_code == 200
    assert pg.one(
        "SELECT last_seen_at > now() - interval '1 minute' AS fresh FROM sessions "
        "WHERE session_hash = %s",
        sha256(token),
    )["fresh"]

    # Well past 30 days after sign-in, but well inside 30 days after the last use.
    age_session(pg, token, 29 * DAY)
    assert cognito_client.get("/api/me").status_code == 200


# ---------------------------------------------------------------- refreshing last_seen_at


def test_last_seen_at_is_refreshed_at_most_once_an_hour(cognito_client, idp, pg):
    sign_in(cognito_client, idp)
    _, token = token_of(cognito_client)
    key = sha256(token)

    age_session(pg, token, 30 * 60)
    before = pg.one("SELECT last_seen_at FROM sessions WHERE session_hash = %s", key)[
        "last_seen_at"
    ]
    assert cognito_client.get("/api/me").status_code == 200
    assert (
        pg.one("SELECT last_seen_at FROM sessions WHERE session_hash = %s", key)["last_seen_at"]
        == before
    )

    age_session(pg, token, 2 * 60 * 60)
    resp = cognito_client.get("/api/me")
    assert resp.status_code == 200
    refreshed = pg.one("SELECT last_seen_at FROM sessions WHERE session_hash = %s", key)[
        "last_seen_at"
    ]
    assert refreshed > before
    assert pg.one(
        "SELECT last_seen_at > now() - interval '1 minute' AS fresh FROM sessions "
        "WHERE session_hash = %s",
        key,
    )["fresh"]
    # The cookie's lifetime moves with it, so the browser keeps the renewed session.
    assert abs(lifetime(session_cookie(resp)) - SESSION_DAYS * DAY) < 120

    assert cognito_client.get("/api/me").status_code == 200
    assert (
        pg.one("SELECT last_seen_at FROM sessions WHERE session_hash = %s", key)["last_seen_at"]
        == refreshed
    )


# ---------------------------------------------------------------- ending a session


def test_sign_out_deletes_the_session_row(cognito_client, idp, pg):
    sign_in(cognito_client, idp)
    assert sessions(pg)

    resp = cognito_client.post("/logout", json={})

    assert resp.status_code == 200
    assert sessions(pg) == []
    assert cognito_client.get("/api/me").status_code == 401


@pytest.fixture
def cognito_admin(monkeypatch):
    """Enough of Cognito's admin API for account deletion: no users to delete."""

    real = BaseClient._make_api_call

    def make_api_call(self, operation_name, api_params):
        if operation_name == "ListUsers":
            return {"Users": []}
        if operation_name == "AdminDeleteUser":
            return {}
        return real(self, operation_name, api_params)

    monkeypatch.setattr(BaseClient, "_make_api_call", make_api_call)


def test_account_delete_deletes_all_of_the_users_sessions(cognito_app, idp, pg, cognito_admin):
    app = cognito_app()
    first, second = app.test_client(), app.test_client()
    sign_in(first, idp)
    sign_in(second, idp)
    user_id = user_id_for(pg)
    assert len(sessions(pg, user_id)) == 2

    resp = first.post("/api/account/delete", json={"confirm": "DELETE"})

    assert resp.status_code == 200
    assert sessions(pg, user_id) == []
    assert first.get("/api/me").status_code == 401
    assert second.get("/api/me").status_code == 401


# ---------------------------------------------------------------- refused cookies


def test_a_forged_token_is_refused(cognito_client, idp, pg):
    sign_in(cognito_client, idp)
    key, _ = token_of(cognito_client)
    with cognito_client.session_transaction() as sess:
        sess[key] = secrets.token_urlsafe(32)

    resp = cognito_client.get("/api/me")

    assert resp.status_code == 401
    assert resp.get_json()["error"] == "not_signed_in"
    assert len(sessions(pg)) == 1  # the real row is untouched


def test_an_old_cookie_with_user_id_and_email_is_refused(cognito_client, idp, pg):
    sign_in(cognito_client, idp)
    user_id = user_id_for(pg)
    with cognito_client.session_transaction() as sess:
        sess.clear()
        sess["user_id"] = user_id
        sess["email"] = "ann@example.com"
        sess["signed_in_at"] = int(time.time())
        sess.permanent = True

    resp = cognito_client.get("/api/me")

    assert resp.status_code == 401
    assert resp.get_json()["error"] == "not_signed_in"


# ---------------------------------------------------------------- the reaper


@pytest.fixture
def reaper_env(aws, db, monkeypatch, tmp_path, patch_everywhere):
    """The reaper handler on the test database, with a config.json research block."""
    monkeypatch.setenv(
        "NEUROLENS_DB_CLUSTER_ARN", "arn:aws:rds:us-east-1:000000000000:cluster:neurolens-db"
    )
    monkeypatch.setenv(
        "NEUROLENS_DB_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:000000000000:secret:x"
    )
    monkeypatch.setenv("NEUROLENS_DB_NAME", "neurolens")
    monkeypatch.setenv("NEUROLENS_S3_BUCKET", aws.bucket)
    monkeypatch.setenv("AWS_REGION", "us-east-1")

    def data_api_database(*args, **kwargs):
        return db

    patch_everywhere(
        "DataApiDatabase", data_api_database, "neurolens.db", "neurolens.lambdas.reaper"
    )
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    (root / "config.json").write_text(json.dumps({"research": {"no_curve_days": 90}}))
    monkeypatch.setenv("NEUROLENS_ROOT", str(root))


def test_the_reaper_deletes_expired_rows_and_keeps_live_ones(aws, db, pg, reaper_env):
    from neurolens.lambdas import reaper

    billing.ensure_user(db, "reaper-user", "reaper@example.com", 0)
    expired = sha256(secrets.token_urlsafe(32))
    live = sha256(secrets.token_urlsafe(32))
    pg.conn.execute(
        "INSERT INTO sessions (session_hash, user_id, created_at, last_seen_at) "
        "VALUES (%s, %s, now() - make_interval(days => 31), "
        "now() - make_interval(days => 31))",
        (expired, "reaper-user"),
    )
    pg.conn.execute(
        "INSERT INTO sessions (session_hash, user_id, created_at, last_seen_at) "
        "VALUES (%s, %s, now() - make_interval(days => 1), now() - make_interval(days => 1))",
        (live, "reaper-user"),
    )

    reaper.handler({}, None)

    assert pg.one("SELECT 1 AS present FROM sessions WHERE session_hash = %s", expired) is None
    assert pg.one("SELECT 1 AS present FROM sessions WHERE session_hash = %s", live) is not None
