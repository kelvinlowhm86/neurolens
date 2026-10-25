"""Who is asking (M3a §2, M3b §2, M3f §6b). The only place the web app reads identity.

Two modes. "dev" (laptop and tests): a fixed development user, allowed only on a local-only
address and never on AWS, so it can never be exposed publicly; every request is signed in as it.
"cognito" (AWS): sign-in through Amazon Cognito's hosted pages (Google or email and password).
The signed cookie holds only a random session token; the server stores its SHA-256 and reads the
user id and email from the database. A session lasts 30 days from its last use.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from flask import current_app, session

from neurolens import billing, settings

LOCAL_HOST = "127.0.0.1"
DEFAULT_AUTH = {"mode": "dev", "dev_user_id": "dev-user", "dev_email": "dev@localhost"}
SESSION_DAYS = billing.SESSION_DAYS
REFRESH_AFTER_S = 60 * 60  # `last_seen_at` moves at most once an hour
SESSION_TOKEN_KEY = "session"
SIGNED_IN_HINT_COOKIE = "nl_signed_in"
SECRET_PARAMETERS = {
    "client_secret": "/neurolens/web/cognito_client_secret",
    "flask_secret_key": "/neurolens/web/flask_secret_key",
}
REQUIRED_COGNITO_SETTINGS = (
    ("auth", "cognito_issuer"),
    ("auth", "cognito_client_id"),
    ("auth", "cognito_domain"),
    ("public_base_url",),
)


def auth_settings(cfg):
    """The auth block with defaults, checked. Raises UnsafeConfigError for the development
    identity on AWS (`deployed`) or on anything but 127.0.0.1, and ValueError for a Cognito
    setting that is missing."""
    cfg = cfg or {}
    auth = {**DEFAULT_AUTH, **(cfg.get("auth") or {})}
    if auth["mode"] == "dev":
        if cfg.get("deployed"):
            raise settings.UnsafeConfigError(
                "auth.mode 'dev' gives everyone the same account, so it never runs on AWS "
                "(NEUROLENS_DEPLOYED is set)."
            )
        host = settings.server_address(cfg)[0]
        if host != LOCAL_HOST:
            raise settings.UnsafeConfigError(
                f"auth.mode 'dev' gives everyone the same account, so server.host must be "
                f"{LOCAL_HOST}, not {host!r}."
            )
        return auth
    if auth["mode"] != "cognito":
        raise ValueError(f"auth.mode must be 'dev' or 'cognito', not {auth['mode']!r}")
    for path in REQUIRED_COGNITO_SETTINGS:
        node = cfg
        for part in path:
            node = (node or {}).get(part)
        if not node:
            raise ValueError(f"Missing required setting in Cognito mode: {'.'.join(path)}")
    return {**auth, "public_base_url": cfg["public_base_url"].rstrip("/")}


def init_auth(app, cfg, ssm_client, *, server_metadata=None):
    """Set the app up for its auth mode. In Cognito mode: the session key and the client secret
    from Parameter Store (failing fast if one is missing), the cookie flags, and the Authlib
    client, which is returned. With `server_metadata` (tests), Authlib uses it instead of
    downloading Cognito's OpenID configuration. In dev mode: a per-process session key."""
    auth = app.config["NEUROLENS_AUTH"]
    if auth["mode"] == "dev":
        app.secret_key = secrets.token_bytes(32)
        app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
        return None

    from authlib.integrations.flask_client import OAuth

    secret = {
        key: settings.get_parameter(ssm_client, name) for key, name in SECRET_PARAMETERS.items()
    }
    app.secret_key = secret["flask_secret_key"]
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=SESSION_DAYS),
        SESSION_REFRESH_EACH_REQUEST=False,
    )
    if server_metadata is None:
        metadata = {
            "server_metadata_url": f"{auth['cognito_issuer']}/.well-known/openid-configuration"
        }
    else:
        metadata = dict(server_metadata)
    oauth = OAuth(app)
    return oauth.register(
        name="cognito",
        client_id=auth["cognito_client_id"],
        client_secret=secret["client_secret"],
        client_kwargs={"scope": "openid email"},
        **metadata,
    )


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _database():
    return current_app.config.get("NEUROLENS_DB")


def start_session(db, user_id):
    """A fresh session for a verified sign-in: a new random token, its hash stored on the server."""
    token = secrets.token_urlsafe(32)
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO sessions (session_hash, user_id, last_seen_at) "
            "VALUES (:session_hash, :user_id, now())",
            {"session_hash": _hash_token(token), "user_id": user_id},
        )
    session.clear()
    session.permanent = True
    session[SESSION_TOKEN_KEY] = token


def end_session(db):
    """Sign-out: remove this browser's session row, so the token can never be used again."""
    token = session.get(SESSION_TOKEN_KEY)
    if isinstance(token, str) and token and db is not None:
        with db.transaction() as tx:
            tx.execute(
                "DELETE FROM sessions WHERE session_hash = :session_hash",
                {"session_hash": _hash_token(token)},
            )
    session.clear()


def set_signed_in_hint(response):
    """The landing page's hint (M3d §8b): no identity, only which buttons show."""
    response.set_cookie(
        SIGNED_IN_HINT_COOKIE,
        "1",
        max_age=SESSION_DAYS * 24 * 60 * 60,
        secure=current_app.config["SESSION_COOKIE_SECURE"],
        httponly=False,
        samesite="Lax",
        path="/",
    )
    return response


def clear_signed_in_hint(response):
    """Delete the landing page's hint whenever the session behind it goes."""
    response.delete_cookie(SIGNED_IN_HINT_COOKIE, path="/")
    return response


def current_user():
    """(user_id, email) of the request's user, or None when nobody is signed in.

    Cognito mode trusts only the server's session row: the token must exist there and its last
    use must be within SESSION_DAYS. The email comes from the database, never the cookie.
    """
    auth = current_app.config["NEUROLENS_AUTH"]
    if auth["mode"] == "dev":
        return auth["dev_user_id"], auth["dev_email"]
    token = session.get(SESSION_TOKEN_KEY)
    db = _database()
    if not isinstance(token, str) or not token or db is None:
        return None
    session_hash = _hash_token(token)
    with db.transaction() as tx:
        rows = tx.execute(
            "SELECT s.user_id, u.email, s.last_seen_at FROM sessions s "
            "JOIN users u ON u.user_id = s.user_id WHERE s.session_hash = :session_hash",
            {"session_hash": session_hash},
        )
    if not rows:
        return None
    row = rows[0]
    now = datetime.now(UTC)
    if now - row["last_seen_at"] >= timedelta(days=SESSION_DAYS):
        return None
    if now - row["last_seen_at"] >= timedelta(seconds=REFRESH_AFTER_S):
        with db.transaction() as tx:
            tx.execute(
                "UPDATE sessions SET last_seen_at = now() WHERE session_hash = :session_hash",
                {"session_hash": session_hash},
            )
        session.permanent = True
        session.modified = True
    return row["user_id"], row["email"]


def email_is_verified(claims):
    """Only True or the string "true" (federated attributes can arrive as strings)."""
    value = claims.get("email_verified")
    return value is True or value == "true"


def logout_url(cfg_auth):
    """Cognito's own logout, so the next sign-in is not silent, then back to the site."""
    from urllib.parse import urlencode

    query = urlencode(
        {
            "client_id": cfg_auth["cognito_client_id"],
            "logout_uri": f"{cfg_auth['public_base_url']}/",
        }
    )
    return f"https://{cfg_auth['cognito_domain']}/logout?{query}"
