"""M3k early access by request (docs/M3k_spec.md §3-§6, §11).

A signed-in user asks for free minutes with a channel link; Josh screens it and grants or
declines. The request is stored once per account, emailed to Josh, and its status is visible in
`/api/me`. A failed email is logged without the requester's details and keeps the request.
"""

import pytest
from botocore.exceptions import ClientError
from conftest import ISSUER, WEB_USER_EMAIL, WEB_USER_ID, fresh_import
from neurolens import billing


class _Access:
    """Import `neurolens.access` on first use, so this file still collects while it is missing."""

    def __getattr__(self, name):
        from neurolens import access

        return getattr(access, name)


access = _Access()

STARTER_CENTS = 10_000
CHANNEL = "https://www.youtube.com/@anncreates"
OTHER_CHANNEL = "https://www.facebook.com/anncreates"
NOTE = "Short cooking videos for busy parents."


# ---------------------------------------------------------------- channel links


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://www.youtube.com/@anncreates", "youtube"),
        ("http://m.facebook.com/anncreates", "facebook"),
        ("https://TikTok.com/@anncreates", "tiktok"),
        ("https://instagram.com/anncreates", "instagram"),
        ("https://youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
    ],
    ids=["youtube", "facebook", "tiktok", "instagram", "youtube-video"],
)
def test_a_channel_link_names_its_platform(url, platform):
    assert access.platform_of(url) == platform


@pytest.mark.parametrize(
    "url",
    [
        "",
        "youtube.com/@anncreates",
        "ftp://youtube.com/@anncreates",
        "https://youtube.com",
        "https://youtube.com/",
        "https://notyoutube.com/@anncreates",
        "https://youtube.com.evil.example/@anncreates",
        "https://vimeo.com/anncreates",
        "https://youtube.com?tab=videos",
    ],
    ids=[
        "empty",
        "no-scheme",
        "wrong-scheme",
        "no-path",
        "root-path",
        "look-alike-host",
        "suffix-host",
        "other-platform",
        "query-only",
    ],
)
def test_anything_else_is_refused_with_one_message(url):
    with pytest.raises(ValueError) as exc:
        access.platform_of(url)

    assert str(exc.value) == ("Paste your YouTube, Facebook, TikTok or Instagram channel link.")


def test_a_link_longer_than_300_characters_is_refused():
    with pytest.raises(ValueError):
        access.platform_of("https://youtube.com/" + "a" * 300)


# ---------------------------------------------------------------- storing a request


def test_a_valid_request_is_stored_as_waiting(db):
    user_id = billing.sign_in(db, ISSUER, "sub-ann", "ann@example.com", 0)

    stored = access.add_request(db, user_id, CHANNEL, f"  {NOTE}  ")

    assert stored["user_id"] == user_id
    assert stored["channel_url"] == CHANNEL
    assert stored["platform"] == "youtube"
    assert stored["note"] == NOTE
    assert stored["status"] == "waiting"
    assert access.request_status(db, user_id) == "waiting"


def test_a_blank_note_is_stored_as_null(db):
    user_id = billing.sign_in(db, ISSUER, "sub-blank", "blank@example.com", 0)

    stored = access.add_request(db, user_id, CHANNEL, "   ")

    assert stored["note"] is None


@pytest.mark.parametrize(
    ("channel_url", "note"),
    [
        ("https://vimeo.com/anncreates", None),
        (CHANNEL, "x" * 501),
    ],
    ids=["bad-link", "long-note"],
)
def test_a_bad_request_is_refused_and_nothing_is_stored(db, channel_url, note):
    user_id = billing.sign_in(db, ISSUER, "sub-bad", "bad@example.com", 0)

    with pytest.raises(ValueError):
        access.add_request(db, user_id, channel_url, note)

    assert access.request_status(db, user_id) is None


def test_a_second_request_is_refused_and_keeps_the_first(db, pg):
    user_id = billing.sign_in(db, ISSUER, "sub-once", "once@example.com", 0)
    access.add_request(db, user_id, CHANNEL, None)

    with pytest.raises(access.AlreadyRequested):
        access.add_request(db, user_id, OTHER_CHANNEL, None)

    [row] = pg.rows("SELECT * FROM access_requests WHERE user_id = %s", user_id)
    assert row["channel_url"] == CHANNEL


def test_a_deleted_account_cannot_ask_and_stores_nothing(db):
    user_id = billing.sign_in(db, ISSUER, "sub-gone", "gone@example.com", 0)
    billing.delete_account(db, user_id)

    with pytest.raises(LookupError, match="no such user"):
        access.add_request(db, user_id, CHANNEL, None)

    assert access.request_status(db, user_id) is None


# ---------------------------------------------------------------- granting and declining


@pytest.mark.parametrize("state", ["waiting", "declined", "none"])
def test_a_grant_marks_a_request_granted_and_leaves_no_request_alone(db, pg, state):
    user_id = billing.sign_in(db, ISSUER, f"sub-{state}", f"{state}@example.com", 0)
    if state != "none":
        access.add_request(db, user_id, CHANNEL, None)
    if state == "declined":
        assert access.decline(db, f"{state}@example.com") is True

    billing.grant_credit(db, f"{state}@example.com", 100)

    expected = "granted" if state != "none" else None
    assert access.request_status(db, user_id) == expected
    if state != "none":
        row = pg.one("SELECT * FROM access_requests WHERE user_id = %s", user_id)
        assert row["status"] == "granted"
        assert row["decided_at"] is not None


def test_declining_a_waiting_request_records_the_decision(db):
    user_id = billing.sign_in(db, ISSUER, "sub-decline", "decline@example.com", 0)
    access.add_request(db, user_id, CHANNEL, None)

    assert access.decline(db, "decline@example.com") is True

    assert access.request_status(db, user_id) == "declined"


def test_decline_changes_only_a_waiting_request(db):
    user_id = billing.sign_in(db, ISSUER, "sub-granted", "granted@example.com", 0)
    access.add_request(db, user_id, CHANNEL, None)
    billing.grant_credit(db, "granted@example.com", 100)

    assert access.decline(db, "granted@example.com") is False
    assert access.decline(db, "nobody@example.com") is False
    assert access.request_status(db, user_id) == "granted"


def test_the_operator_list_holds_only_waiting_requests_oldest_first(db, pg):
    first = billing.sign_in(db, ISSUER, "sub-old", "old@example.com", 0)
    second = billing.sign_in(db, ISSUER, "sub-new", "new@example.com", 0)
    granted = billing.sign_in(db, ISSUER, "sub-done", "done@example.com", 0)
    access.add_request(db, first, CHANNEL, "old request")
    access.add_request(db, second, OTHER_CHANNEL, "new request")
    access.add_request(db, granted, CHANNEL, None)
    billing.grant_credit(db, "done@example.com", 100)
    pg.conn.execute(
        "UPDATE access_requests SET created_at = now() - interval '1 hour' WHERE user_id = %s",
        (first,),
    )

    rows = access.waiting_requests(db)

    assert [row["email"] for row in rows] == ["old@example.com", "new@example.com"]
    assert rows[0]["channel_url"] == CHANNEL
    assert rows[0]["note"] == "old request"


def test_deleting_the_account_removes_its_access_request(db, pg):
    user_id = billing.sign_in(db, ISSUER, "sub-delete", "delete@example.com", 0)
    access.add_request(db, user_id, CHANNEL, NOTE)

    billing.delete_account(db, user_id)

    assert pg.one("SELECT * FROM access_requests WHERE user_id = %s", user_id) is None


def test_a_request_that_raced_the_delete_does_not_stop_remove_deleted_account(db, pg):
    user_id = billing.sign_in(db, ISSUER, "sub-race", "race@example.com", 0)
    billing.delete_account(db, user_id)
    pg.conn.execute(
        "INSERT INTO access_requests (user_id, channel_url, platform, note) "
        "VALUES (%s, %s, 'youtube', NULL)",
        (user_id, CHANNEL),
    )

    assert billing.remove_deleted_account(db, user_id) is True

    assert pg.one("SELECT * FROM users WHERE user_id = %s", user_id) is None
    assert pg.one("SELECT * FROM access_requests WHERE user_id = %s", user_id) is None


# ---------------------------------------------------------------- the request route


class RecordingNotifier:
    def __init__(self):
        self.calls = []

    def __call__(self, request, email):
        self.calls.append((request, email))


def raising_notifier(request, email):
    raise RuntimeError("SNS is unavailable")


def dev_client(aws, db, make_cfg, tmp_path, *, notify=None):
    from neurolens.web.app import create_app

    cfg = make_cfg(
        billing={"starter_cents": STARTER_CENTS, "invite_cents": 1000},
    )
    kwargs = {} if notify is None else {"notify": notify}
    app = create_app(
        cfg=cfg,
        data_dir=tmp_path / "data",
        db=db,
        s3_client=aws.s3,
        **kwargs,
    )
    app.config["TESTING"] = True
    return app.test_client()


def request_body(**overrides):
    body = {"channel_url": CHANNEL, "note": NOTE}
    body.update(overrides)
    return body


def test_a_valid_request_is_waiting_committed_and_emailed_once(db, pg, aws, make_cfg, tmp_path):
    notifier = RecordingNotifier()
    client = dev_client(aws, db, make_cfg, tmp_path, notify=notifier)
    billing.ensure_user(db, WEB_USER_ID, WEB_USER_EMAIL, STARTER_CENTS)

    resp = client.post("/api/access-request", json=request_body())

    assert resp.status_code == 201
    assert resp.get_json() == {"request": "waiting"}
    assert access.request_status(db, WEB_USER_ID) == "waiting"
    row = pg.one("SELECT * FROM access_requests WHERE user_id = %s", WEB_USER_ID)
    assert len(notifier.calls) == 1
    emailed, email = notifier.calls[0]
    assert email == WEB_USER_EMAIL
    assert emailed["user_id"] == WEB_USER_ID
    assert emailed["channel_url"] == row["channel_url"]
    assert emailed["status"] == "waiting"


@pytest.mark.parametrize(
    "body",
    [
        request_body(channel_url="https://vimeo.com/anncreates"),
        request_body(note="x" * 501),
    ],
    ids=["bad-link", "long-note"],
)
def test_a_bad_request_is_400_and_nothing_is_stored(db, aws, make_cfg, tmp_path, body):
    client = dev_client(aws, db, make_cfg, tmp_path)
    billing.ensure_user(db, WEB_USER_ID, WEB_USER_EMAIL, STARTER_CENTS)

    resp = client.post("/api/access-request", json=body)

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "bad_request"
    assert access.request_status(db, WEB_USER_ID) is None


def test_a_second_request_is_409(db, aws, make_cfg, tmp_path):
    client = dev_client(aws, db, make_cfg, tmp_path)
    billing.ensure_user(db, WEB_USER_ID, WEB_USER_EMAIL, STARTER_CENTS)
    assert client.post("/api/access-request", json=request_body()).status_code == 201

    resp = client.post("/api/access-request", json=request_body())

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "already_requested"


def test_a_signed_out_user_cannot_ask(cognito_app, db):
    client = cognito_app().test_client()

    resp = client.post("/api/access-request", json=request_body())

    assert resp.status_code == 401
    assert resp.get_json()["error"] == "not_signed_in"


def test_a_failed_email_still_returns_201_keeps_the_row_and_logs_no_details(
    db, aws, make_cfg, tmp_path, caplog
):
    client = dev_client(aws, db, make_cfg, tmp_path, notify=raising_notifier)
    billing.ensure_user(db, WEB_USER_ID, WEB_USER_EMAIL, STARTER_CENTS)

    with caplog.at_level("ERROR"):
        resp = client.post("/api/access-request", json=request_body())

    assert resp.status_code == 201
    assert resp.get_json() == {"request": "waiting"}
    assert access.request_status(db, WEB_USER_ID) == "waiting"
    assert any(record.levelname == "ERROR" for record in caplog.records)
    log = caplog.text
    assert "access request email failed for user" in log
    assert WEB_USER_ID in log
    assert WEB_USER_EMAIL not in log
    assert CHANNEL not in log
    assert NOTE not in log


# ---------------------------------------------------------------- /api/me


@pytest.mark.parametrize(
    ("state", "request_status", "funded"),
    [
        ("none", None, False),
        ("waiting", "waiting", False),
        ("granted", "granted", True),
        ("declined", "declined", False),
    ],
)
def test_me_reports_funding_and_the_request_status(
    db, aws, make_cfg, tmp_path, state, request_status, funded
):
    client = dev_client(aws, db, make_cfg, tmp_path)
    billing.ensure_user(db, WEB_USER_ID, WEB_USER_EMAIL, STARTER_CENTS)
    if state != "none":
        access.add_request(db, WEB_USER_ID, CHANNEL, None)
    if state == "granted":
        billing.grant_credit(db, WEB_USER_EMAIL, 100)
    elif state == "declined":
        access.decline(db, WEB_USER_EMAIL)

    resp = client.get("/api/me")

    assert resp.status_code == 200
    assert resp.get_json()["access"] == {"funded": funded, "request": request_status}


# ---------------------------------------------------------------- the deployed launcher


def test_the_deployed_launcher_fails_to_start_without_the_request_topic_arn(lambda_env, ssm):
    ssm.put_parameter(
        Name="/neurolens/web/youtube_api_key", Value="the-video-key", Type="SecureString"
    )
    module = fresh_import("neurolens.web.lambda_handler")

    with pytest.raises(ClientError) as exc:
        module._load_settings()

    assert exc.value.response["Error"]["Code"] == "ParameterNotFound"
    assert "/neurolens/web/request_topic_arn" in exc.value.response["Error"]["Message"]
