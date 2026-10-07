"""Who is asking (M3a §2). The only place the web app reads identity.

M3a has one mode, "dev": a fixed development user, allowed only on a local-only address so it
can never be exposed publicly. M3b adds "google" behind the same current_user().
"""

from flask import current_app

from neurolens import settings

LOCAL_HOST = "127.0.0.1"
DEFAULT_AUTH = {"mode": "dev", "dev_user_id": "dev-user", "dev_email": "dev@localhost"}


def auth_settings(cfg):
    """The auth block with defaults, checked. Raises UnsafeConfigError for the development
    identity on anything but 127.0.0.1."""
    auth = {**DEFAULT_AUTH, **((cfg or {}).get("auth") or {})}
    if auth["mode"] != "dev":
        raise ValueError(f"auth.mode must be 'dev' (M3a), not {auth['mode']!r}")
    host = settings.server_address(cfg)[0]
    if host != LOCAL_HOST:
        raise settings.UnsafeConfigError(
            f"auth.mode 'dev' gives everyone the same account, so server.host must be "
            f"{LOCAL_HOST}, not {host!r}."
        )
    return auth


def current_user():
    """(user_id, email) of the request's user, or None when nobody is signed in."""
    auth = current_app.config["NEUROLENS_AUTH"]
    return auth["dev_user_id"], auth["dev_email"]
