"""M3b sign-in and the app-wide rules (Flask test client + moto + PostgreSQL). Written first from
docs/M3b_spec.md §2b, §2c, §3a, §8 and §11: the callback through §8's seam (Authlib's real state,
nonce and signature checks against a test RSA key; only HTTP is faked), the `email_verified`
rule, failures without a session, the login and logout URLs built from config, the 30-day server
session (M3f §6b), 401 on every /api/* route, cookie flags, JSON-only POSTs, no CORS,
/healthz without the database, and fail-fast startup.
"""

import re
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import (
    CLIENT_ID,
    COGNITO_DOMAIN,
    ISSUER,
    MISSING,
    PUBLIC_BASE_URL,
    SERVER_METADATA,
    WEB_PARAMETERS,
    sign_in,
)
from neurolens import billing, pricing, settings
from neurolens.web.app import create_app

SESSION_KEYS = {"session"}
HOUR = 3600
SESSION_DAYS = 30
PUBLIC_RULES = {"/", "/app", "/login", "/auth/callback", "/logout", "/healthz"}
PUBLIC_PREFIXES = ("/static/", "/data/")
STATIC = Path(__file__).resolve().parent.parent / "static"


def session_of(client):
    """The signed session's own keys (Flask's `_permanent` marker aside)."""
    with client.session_transaction() as sess:
        return {k: v for k, v in sess.items() if k != "_permanent"}


def user_count(pg):
    return pg.one("SELECT count(*) AS n FROM users")["n"]


def api_rules(app):
    """(url, method) for every /api/* route, with a UUID for each URL variable."""
    found = []
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/"):
            continue
        url = re.sub(r"<[^>]+>", str(uuid.uuid4()), rule.rule)
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            found.append((url, method))
    return found


def call(client, url, method, **kwargs):
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        kwargs.setdefault("json", {})
    return client.open(url, method=method, **kwargs)


class ExplodingDatabase:
    """A Database whose every use fails the test."""

    def transaction(self):
        raise AssertionError("the database must not be used here")


# ---------------------------------------------------------------- the callback


def test_callback_signs_in_and_stores_only_a_session_token(cognito_client, idp):
    resp = sign_in(cognito_client, idp, email="ann@example.com")
    assert resp.status_code in (302, 303)
    assert urlparse(resp.headers["Location"]).path == "/app"
    sess = session_of(cognito_client)
    assert set(sess) == SESSION_KEYS
    assert isinstance(sess["session"], str) and sess["session"]
    me = cognito_client.get("/api/me")
    assert me.status_code == 200
    assert uuid.UUID(me.get_json()["user_id"])
    assert me.get_json()["email"] == "ann@example.com"


def test_callback_clears_any_earlier_session(cognito_client, idp):
    with cognito_client.session_transaction() as sess:
        sess["user_id"] = "someone-else"
        sess["stale"] = "left over"
    sign_in(cognito_client, idp)
    sess = session_of(cognito_client)
    assert set(sess) == SESSION_KEYS
    assert sess["session"] != "someone-else"


def test_callback_makes_a_permanent_session(cognito_client, idp):
    sign_in(cognito_client, idp)
    with cognito_client.session_transaction() as sess:
        assert sess.permanent


def test_callback_redirects_to_app_never_to_a_url_from_the_request(cognito_client, idp):
    login = cognito_client.get("/login")
    query = parse_qs(urlparse(login.headers["Location"]).query)
    now = int(time.time())
    idp.claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "google_1001",
        "email": "ann@example.com",
        "email_verified": True,
        "iat": now,
        "exp": now + 3600,
        "nonce": query["nonce"][0],
    }
    resp = cognito_client.get(
        "/auth/callback",
        query_string={
            "code": "c",
            "state": query["state"][0],
            "next": "https://evil.example/",
            "redirect_uri": "https://evil.example/",
        },
    )
    location = urlparse(resp.headers["Location"])
    assert location.path == "/app"
    assert location.netloc in ("", urlparse(PUBLIC_BASE_URL).netloc)


@pytest.mark.parametrize("verified", [True, "true"], ids=["bool", "string"])
def test_email_verified_true_or_the_string_true_is_accepted(cognito_client, idp, verified):
    resp = sign_in(cognito_client, idp, email_verified=verified)
    assert resp.status_code in (302, 303)
    assert set(session_of(cognito_client)) == SESSION_KEYS


@pytest.mark.parametrize("verified", [False, "false", "True", "TRUE", 1, MISSING], ids=repr)
def test_anything_else_in_email_verified_is_403_with_no_session(cognito_client, idp, pg, verified):
    resp = sign_in(cognito_client, idp, email_verified=verified)
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "email_not_verified"
    assert "session" not in session_of(cognito_client)
    assert user_count(pg) == 0
    assert cognito_client.get("/api/me").status_code == 401


def test_a_wrong_state_is_400_sign_in_failed_with_no_session(cognito_client, idp, pg):
    resp = sign_in(cognito_client, idp, state="not-the-state-login-sent")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "sign_in_failed"
    assert "session" not in session_of(cognito_client)
    assert user_count(pg) == 0


def test_a_token_signed_by_the_wrong_key_is_400_sign_in_failed(cognito_client, idp, pg):
    resp = sign_in(cognito_client, idp, signing_key=idp.wrong_key)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "sign_in_failed"
    assert "session" not in session_of(cognito_client)
    assert user_count(pg) == 0


@pytest.mark.parametrize(
    "claim, value",
    [("aud", "another-client"), ("iss", "https://evil.example"), ("nonce", "wrong-nonce")],
)
def test_a_token_for_another_client_issuer_or_nonce_is_400(cognito_client, idp, pg, claim, value):
    resp = sign_in(cognito_client, idp, **{claim: value})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "sign_in_failed"
    assert "session" not in session_of(cognito_client)
    assert user_count(pg) == 0


def test_a_cancelled_sign_in_is_400_sign_in_failed(cognito_client, idp, pg):
    login = cognito_client.get("/login")
    state = parse_qs(urlparse(login.headers["Location"]).query)["state"][0]
    resp = cognito_client.get(
        "/auth/callback", query_string={"error": "access_denied", "state": state}
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "sign_in_failed"
    assert "session" not in session_of(cognito_client)
    assert user_count(pg) == 0


def test_a_callback_without_a_login_is_400(cognito_client, pg):
    resp = cognito_client.get("/auth/callback", query_string={"code": "c", "state": "s"})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "sign_in_failed"
    assert user_count(pg) == 0


def test_google_and_email_sign_ins_with_one_verified_email_reach_one_account(cognito_app, idp):
    """§2c's linking rule end to end: the second way in finds the first account."""
    app = cognito_app()
    google, email = app.test_client(), app.test_client()
    sign_in(google, idp, sub="google_1001", email="Ann@Example.com")
    sign_in(email, idp, sub=str(uuid.uuid4()), email="ann@example.com")
    first = google.get("/api/me").get_json()
    second = email.get("/api/me").get_json()
    assert first["user_id"] == second["user_id"]
    assert first["balance"] == second["balance"]


# ---------------------------------------------------------------- login and logout


def test_login_redirects_with_exactly_the_configured_callback(cognito_app):
    """Built from public_base_url, never from the request's host (CloudFront forwards to Lambda
    under another host name)."""
    client = cognito_app().test_client()
    resp = client.get("/login", base_url="https://abcdefgh.lambda-url.us-east-1.on.aws")
    assert resp.status_code in (302, 303)
    location = urlparse(resp.headers["Location"])
    assert (
        f"{location.scheme}://{location.netloc}{location.path}"
        == (SERVER_METADATA["authorization_endpoint"])
    )
    query = parse_qs(location.query)
    assert query["redirect_uri"] == [f"{PUBLIC_BASE_URL}/auth/callback"]
    assert query["client_id"] == [CLIENT_ID]
    assert query["response_type"] == ["code"]
    assert set(query["scope"][0].split()) == {"openid", "email"}
    assert query["state"][0] and query["nonce"][0]


def test_logout_clears_the_session_and_returns_cognitos_logout_url(cognito_client, idp):
    sign_in(cognito_client, idp)
    assert cognito_client.get("/api/me").status_code == 200
    resp = cognito_client.post("/logout", json={})
    assert resp.status_code == 200
    url = urlparse(resp.get_json()["logout_url"])
    assert (url.scheme, url.netloc, url.path) == ("https", COGNITO_DOMAIN, "/logout")
    assert parse_qs(url.query) == {"client_id": [CLIENT_ID], "logout_uri": [f"{PUBLIC_BASE_URL}/"]}
    assert "session" not in session_of(cognito_client)
    assert cognito_client.get("/api/me").status_code == 401


# ---------------------------------------------------------------- the session lifetime


def test_a_session_survives_a_new_app_with_the_same_secret_key(cognito_app, idp):
    """The Flask key comes from Parameter Store, so a redeploy keeps everyone signed in."""
    resp = sign_in(cognito_app().test_client(), idp)
    cookies = [c.split(";")[0].split("=", 1) for c in resp.headers.getlist("Set-Cookie")]
    assert cookies
    second = cognito_app().test_client()
    for name, value in cookies:
        second.set_cookie(name, value)
    assert second.get("/api/me").status_code == 200


# ---------------------------------------------------------------- every /api/* route needs sign-in

PUBLIC_API = {("/api/limits", "GET"), ("/api/wake", "POST")}


def test_every_api_route_is_401_json_when_signed_out(cognito_app):
    app = cognito_app()
    client = app.test_client()
    rules = [rule for rule in api_rules(app) if rule not in PUBLIC_API]
    paths = {re.sub(r"[0-9a-f-]{36}", "<id>", url) for url, _ in rules}
    assert {
        "/api/me",
        "/api/jobs",
        "/api/uploads/presign",
        "/api/jobs/<id>/status",
        "/api/jobs/<id>/result",
        "/api/jobs/<id>/result.csv",
    } <= paths
    for url, method in rules:
        resp = call(client, url, method)
        assert resp.status_code == 401, (method, url)
        assert resp.is_json, (method, url)
        assert resp.get_json()["error"] == "not_signed_in", (method, url)


def test_only_the_listed_routes_are_outside_api(cognito_app):
    for rule in cognito_app().url_map.iter_rules():
        path = rule.rule
        if path.startswith("/api/"):
            continue
        assert path in PUBLIC_RULES or path.startswith(PUBLIC_PREFIXES), path


def test_the_public_routes_stay_public(cognito_app, tmp_path):
    client = cognito_app().test_client()
    (tmp_path / "data" / "ok.txt").write_text("fine")
    assert client.get("/").status_code == 200
    assert client.get("/static/chart.min.js").status_code == 200
    assert client.get("/data/ok.txt").data == b"fine"
    assert client.get("/login").status_code in (302, 303)
    assert client.get("/auth/callback").status_code == 400  # refused, but not 401
    assert client.post("/logout", json={}).status_code == 200
    assert client.get("/healthz").status_code == 200


def test_app_serves_app_html_when_signed_out(cognito_client):
    resp = cognito_client.get("/app")
    assert resp.status_code == 200
    assert resp.data == (STATIC / "app.html").read_bytes()
    resp.close()


def test_slash_serves_the_landing_page(cognito_client):
    resp = cognito_client.get("/")
    assert resp.status_code == 200
    assert resp.data == (STATIC / "index.html").read_bytes()
    resp.close()


def test_limits_answer_signed_out_and_signed_in(cognito_client, idp):
    """M3d §6: the page needs colour_range to colour a sample's brain before sign-in."""
    expected = {
        "max_video_duration_seconds": 120,
        "max_upload_bytes": 300000000,
        "colour_range": 0.5,
        "price": {
            "cents_per_block": pricing.CENTS_PER_BLOCK,
            "block_seconds": pricing.BLOCK_SECONDS,
            "allowance_seconds": pricing.ALLOWANCE_SECONDS,
        },
    }
    resp = cognito_client.get("/api/limits")
    assert resp.status_code == 200
    assert resp.get_json() == expected
    sign_in(cognito_client, idp)
    assert cognito_client.get("/api/limits").get_json() == expected


def test_dev_mode_is_always_signed_in(aws, db, make_cfg, tmp_path):
    """§2b: dev mode keeps M3a's development user; every request is signed in as it."""
    app = create_app(cfg=make_cfg(), data_dir=tmp_path / "data", db=db, s3_client=aws.s3)
    client = app.test_client()
    assert client.get("/api/limits").status_code == 200
    resp = client.get("/api/me")
    assert resp.status_code == 200
    assert resp.get_json()["user_id"] == "web-user"


# ---------------------------------------------------------------- cookies, CSRF, CORS


def test_session_cookie_is_secure_httponly_and_samesite_lax(cognito_client, idp):
    resp = sign_in(cognito_client, idp)
    cookies = resp.headers.getlist("Set-Cookie")
    assert cookies
    for cookie in cookies:
        if cookie.startswith(f"{HINT_COOKIE}="):
            continue  # the landing page's hint is readable by design (its own test below)
        flags = {part.strip().split("=")[0].lower(): part.strip() for part in cookie.split(";")}
        assert "secure" in flags, cookie
        assert "httponly" in flags, cookie
        assert flags.get("samesite", "").lower() == "samesite=lax", cookie


@pytest.mark.parametrize(
    "data, content_type",
    [
        ('{"content_type": "video/mp4"}', "text/plain"),
        ("content_type=video/mp4", "application/x-www-form-urlencoded"),
        ("--b--", "multipart/form-data; boundary=b"),
        ('{"content_type": "video/mp4"}', None),
    ],
    ids=["text", "form", "multipart", "none"],
)
def test_a_post_without_a_json_content_type_is_415(cognito_app, idp, data, content_type):
    app = cognito_app()
    client = app.test_client()
    sign_in(client, idp, email="team@example.com")
    posts = [url for url, method in api_rules(app) if method == "POST"]
    assert "/api/uploads/presign" in posts
    for url in posts:
        kwargs = {"data": data}
        if content_type:
            kwargs["content_type"] = content_type
        resp = client.post(url, **kwargs)
        assert resp.status_code == 415, url


def test_a_post_without_a_json_content_type_is_415_in_dev_mode(aws, db, make_cfg, tmp_path, pg):
    app = create_app(cfg=make_cfg(), data_dir=tmp_path / "data", db=db, s3_client=aws.s3)
    resp = app.test_client().post(
        "/api/uploads/presign",
        data='{"content_type": "video/mp4", "client_duration_seconds": 10}',
        content_type="text/plain",
    )
    assert resp.status_code == 415
    assert pg.rows("SELECT job_id FROM jobs") == []


def test_no_response_carries_cors_headers(cognito_app, idp, aws, db, make_cfg, tmp_path):
    dev = create_app(cfg=make_cfg(), data_dir=tmp_path / "data", db=db, s3_client=aws.s3)
    cognito = cognito_app().test_client()
    sign_in(cognito, idp)
    origin = {"Origin": "https://evil.example"}
    preflight = {**origin, "Access-Control-Request-Method": "POST"}
    for client in (dev.test_client(), cognito):
        for resp in (
            client.get("/api/me", headers=origin),
            client.get("/api/limits", headers=origin),
            client.options("/api/uploads/presign", headers=preflight),
            client.get("/healthz", headers=origin),
        ):
            assert not [h for h in resp.headers.keys() if h.lower().startswith("access-control")]


# ---------------------------------------------------------------- /healthz


def test_healthz_answers_without_the_database(cognito_app, aws, make_cfg, tmp_path):
    cognito = cognito_app(database=ExplodingDatabase()).test_client()
    dev = create_app(
        cfg=make_cfg(), data_dir=tmp_path / "data", db=ExplodingDatabase(), s3_client=aws.s3
    ).test_client()
    for client in (cognito, dev):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True}


# ---------------------------------------------------------------- startup


def test_cognito_mode_starts_with_every_setting_and_parameter(cognito_app):
    cognito_app()


@pytest.mark.parametrize(
    "path",
    [
        ("auth", "cognito_issuer"),
        ("auth", "cognito_client_id"),
        ("auth", "cognito_domain"),
        ("public_base_url",),
        ("aws", "gpu_endpoint"),
    ],
    ids=lambda p: ".".join(p),
)
def test_cognito_mode_with_a_missing_setting_fails_fast(cognito_app, cognito_cfg, path):
    cfg = cognito_cfg()
    node = cfg
    for part in path[:-1]:
        node = node[part]
    del node[path[-1]]
    with pytest.raises(Exception):  # noqa: B017 - the spec fixes no exception type
        cognito_app(cfg)


@pytest.mark.parametrize("name", list(WEB_PARAMETERS))
def test_cognito_mode_with_a_missing_parameter_fails_fast(cognito_app, ssm, name):
    ssm.delete_parameter(Name=name)
    with pytest.raises(Exception):  # noqa: B017
        cognito_app()


def test_dev_mode_with_neurolens_deployed_refuses_to_start(make_cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("NEUROLENS_DEPLOYED", "1")
    cfg = settings.apply_env(make_cfg())
    assert cfg["deployed"] is True
    with pytest.raises(settings.UnsafeConfigError):
        create_app(cfg=cfg, data_dir=tmp_path / "data")


@pytest.mark.parametrize("host", ["0.0.0.0", "10.0.0.5"])
def test_dev_mode_on_a_non_local_host_still_refuses_to_start(make_cfg, tmp_path, host):
    with pytest.raises(settings.UnsafeConfigError):
        create_app(cfg=make_cfg(server={"host": host, "port": 5003}), data_dir=tmp_path / "data")


# ---------------------------------------------------------------- settings


def test_get_parameter_returns_the_decrypted_value(ssm):
    for name, value in WEB_PARAMETERS.items():
        assert settings.get_parameter(ssm, name) == value


def test_get_parameter_for_a_missing_name_raises(ssm):
    with pytest.raises(Exception):  # noqa: B017
        settings.get_parameter(ssm, "/neurolens/web/no_such_parameter")


@pytest.mark.parametrize(
    "var, path",
    [
        ("NEUROLENS_AUTH_MODE", ("auth", "mode")),
        ("NEUROLENS_COGNITO_ISSUER", ("auth", "cognito_issuer")),
        ("NEUROLENS_COGNITO_CLIENT_ID", ("auth", "cognito_client_id")),
        ("NEUROLENS_COGNITO_DOMAIN", ("auth", "cognito_domain")),
        ("NEUROLENS_PUBLIC_BASE_URL", ("public_base_url",)),
        ("NEUROLENS_GPU_ENDPOINT", ("aws", "gpu_endpoint")),
    ],
    ids=lambda x: x if isinstance(x, str) else ".".join(x),
)
def test_apply_env_maps_the_m3b_variables(monkeypatch, var, path):
    monkeypatch.setenv(var, "the-value")
    node = settings.apply_env({"auth": {"mode": "dev"}})
    for part in path:
        node = node[part]
    assert node == "the-value"


def test_apply_env_maps_neurolens_deployed_to_deployed_true(monkeypatch):
    assert not settings.apply_env({}).get("deployed")
    monkeypatch.setenv("NEUROLENS_DEPLOYED", "1")
    assert settings.apply_env({})["deployed"] is True


# ---------------------------------------------------------------- the landing-page hint


HINT_COOKIE = "nl_signed_in"


def hint_cookie(resp):
    for cookie in resp.headers.getlist("Set-Cookie"):
        if cookie.startswith(f"{HINT_COOKIE}="):
            return cookie
    return None


def cookie_flags(cookie):
    name_value, *parts = [part.strip() for part in cookie.split(";")]
    return name_value, {part.split("=")[0].lower(): part for part in parts}


def test_sign_in_sets_the_hint_with_the_session_flags(cognito_client, idp):
    cookie = hint_cookie(sign_in(cognito_client, idp))
    assert cookie is not None
    name_value, flags = cookie_flags(cookie)
    assert name_value == f"{HINT_COOKIE}=1"
    assert flags["path"].lower() == "path=/"
    assert flags["max-age"].lower() == f"max-age={SESSION_DAYS * 24 * HOUR}"
    assert flags["samesite"].lower() == "samesite=lax"
    assert "secure" in flags
    assert "httponly" not in flags


def test_logout_deletes_the_hint(cognito_client, idp):
    sign_in(cognito_client, idp)
    resp = cognito_client.post("/logout", json={})
    assert resp.status_code == 200
    cookie = hint_cookie(resp)
    assert cookie is not None
    _, flags = cookie_flags(cookie)
    assert flags["max-age"].lower() == "max-age=0"


def test_account_deletion_deletes_the_hint(cognito_client, idp, monkeypatch):
    from botocore.client import BaseClient

    real = BaseClient._make_api_call

    def no_users(self, operation_name, api_params):
        if operation_name == "ListUsers":
            return {"Users": []}
        if operation_name == "AdminDeleteUser":
            return {}
        return real(self, operation_name, api_params)

    monkeypatch.setattr(BaseClient, "_make_api_call", no_users)
    sign_in(cognito_client, idp)
    resp = cognito_client.post("/api/account/delete", json={"confirm": "DELETE"})
    assert resp.status_code == 200
    cookie = hint_cookie(resp)
    assert cookie is not None
    _, flags = cookie_flags(cookie)
    assert flags["max-age"].lower() == "max-age=0"


def test_a_session_for_a_deleted_user_401s_and_deletes_the_hint(cognito_client, idp, db, pg):
    sign_in(cognito_client, idp, email="ann@example.com")
    user_id = pg.one("SELECT user_id FROM users WHERE lower(email) = lower(%s)", "ann@example.com")[
        "user_id"
    ]
    billing.delete_account(db, user_id)

    resp = cognito_client.get("/api/me")

    assert resp.status_code == 401
    cookie = hint_cookie(resp)
    assert cookie is not None
    _, flags = cookie_flags(cookie)
    assert flags["max-age"].lower() == "max-age=0"


def test_every_page_post_goes_through_post_json():
    """M3b: behind CloudFront's origin access control, a POST without `x-amz-content-sha256`
    is refused before it reaches the app, so every page POST must use the hashing helper."""
    for page in STATIC.glob("*.html"):
        text = page.read_text()
        posts = [m.start() for m in re.finditer(r"""method:\s*['"]POST['"]""", text)]
        helper = text.find("async function postJSON(")
        allowed = [p for p in posts if helper != -1 and helper < p < text.find("\n}\n", helper)]
        assert posts == allowed, f"{page.name}: a POST bypasses postJSON"
