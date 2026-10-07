"""Who is asking (M3a §2, M3b §2). The only place the web app reads identity.

Two modes. "dev" (laptop and tests): a fixed development user, allowed only on a local-only
address and never on AWS, so it can never be exposed publicly; every request is signed in as it.
"cognito" (AWS): sign-in through Amazon Cognito's hosted pages (Google or email and password),
with a signed session cookie that holds only the user's ID, email and sign-in time.
"""

import secrets
import time
from datetime import timedelta

from flask import current_app, session

from neurolens import settings

LOCAL_HOST = "127.0.0.1"
DEFAULT_AUTH = {"mode": "dev", "dev_user_id": "dev-user", "dev_email": "dev@localhost"}
SESSION_HOURS = 12
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
        PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSION_HOURS),
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


def start_session(user_id, email):
    """A fresh session for a verified sign-in: nothing from before it survives."""
    session.clear()
    session.permanent = True
    session.update(user_id=user_id, email=email, signed_in_at=int(time.time()))


def current_user():
    """(user_id, email) of the request's user, or None when nobody is signed in. A Cognito
    session ends 12 hours after sign-in however often it is used."""
    auth = current_app.config["NEUROLENS_AUTH"]
    if auth["mode"] == "dev":
        return auth["dev_user_id"], auth["dev_email"]
    signed_in_at = session.get("signed_in_at")
    if not isinstance(signed_in_at, int) or "user_id" not in session:
        return None
    if time.time() - signed_in_at >= SESSION_HOURS * 3600:
        return None
    return session["user_id"], session["email"]


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
