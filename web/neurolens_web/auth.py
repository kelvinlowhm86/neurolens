"""
Sign-in: Google OIDC (production), dev-login (local only), and a load-test header (Experiment 2).

The session cookie holds only the user id. Everything else is read from the database per request,
which is what keeps the web tier stateless.
"""

import functools
import hmac
import logging
from uuid import UUID, uuid4

from authlib.integrations.flask_client import OAuth
from flask import Blueprint, current_app, g, jsonify, redirect, request, session

from neurolens_common import db, ledger
from neurolens_common.config import Settings

logger = logging.getLogger("neurolens.web.auth")

bp = Blueprint("auth", __name__)
oauth = OAuth()

LOADTEST_TOPUP = 10_000


def init_oauth(app, cfg: Settings) -> None:
    oauth.init_app(app)
    if cfg.google_client_id:
        oauth.register(
            "google",
            client_id=cfg.google_client_id,
            client_secret=cfg.google_client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )


def _cfg() -> Settings:
    return current_app.extensions["nl_cfg"]


# ─── Users ─────────────────────────────────────────────────────────────────


def upsert_user(conn, *, email: str, google_sub: str | None, name: str | None, avatar_url: str | None) -> UUID:
    """Create or update a user; new users get the signup bonus exactly once."""
    row = conn.execute(
        """INSERT INTO users (email, google_sub, name, avatar_url, last_login_at)
           VALUES (%s, %s, %s, %s, now())
           ON CONFLICT (email) DO UPDATE
              SET google_sub = COALESCE(EXCLUDED.google_sub, users.google_sub),
                  name = COALESCE(EXCLUDED.name, users.name),
                  avatar_url = COALESCE(EXCLUDED.avatar_url, users.avatar_url),
                  last_login_at = now()
           RETURNING id""",
        (email, google_sub, name, avatar_url),
    ).fetchone()
    uid = row["id"]
    bonus = _cfg().signup_bonus_credits
    if bonus > 0:
        ledger.grant(conn, uid, bonus, "GRANT", f"signup:{uid}")   # no-op after the first time
    return uid


def current_user_id() -> UUID | None:
    if "user_id" in g:
        return g.user_id
    uid = _loadtest_user() or session.get("user_id")
    g.user_id = UUID(str(uid)) if uid else None
    return g.user_id


def _loadtest_user() -> UUID | None:
    """X-Loadtest-Token + X-Loadtest-User → a dedicated, auto-topped-up user. Off unless the secret is set."""
    token = _cfg().loadtest_token
    supplied = request.headers.get("X-Loadtest-Token")
    if not token or not supplied or not hmac.compare_digest(token, supplied):
        return None
    name = request.headers.get("X-Loadtest-User", "lt-0")[:40]
    with db.transaction() as conn:
        uid = upsert_user(conn, email=f"{name}@loadtest.local", google_sub=None, name=name, avatar_url=None)
        bal = ledger.balance(conn, uid)["credits_available"]
        if bal < LOADTEST_TOPUP // 2:
            ledger.grant(conn, uid, LOADTEST_TOPUP, "GRANT", f"loadtest:{uuid4()}")
    return uid


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if current_user_id() is None:
            return jsonify(error={"code": "UNAUTHENTICATED", "message": "Sign in first"}), 401
        return fn(*args, **kwargs)
    return wrapper


# ─── Routes ────────────────────────────────────────────────────────────────


@bp.get("/auth/login")
def login():
    if "google" not in oauth._clients:
        return jsonify(error={"code": "OAUTH_NOT_CONFIGURED",
                              "message": "Google sign-in is not configured; use dev-login locally"}), 503
    return oauth.google.authorize_redirect(_cfg().public_base_url.rstrip("/") + "/auth/callback")


@bp.get("/auth/callback")
def callback():
    token = oauth.google.authorize_access_token()
    info = token.get("userinfo") or oauth.google.userinfo()
    if not info.get("email_verified", True):
        return jsonify(error={"code": "EMAIL_UNVERIFIED", "message": "Google account email is not verified"}), 403
    with db.transaction() as conn:
        uid = upsert_user(conn, email=info["email"], google_sub=info["sub"],
                          name=info.get("name"), avatar_url=info.get("picture"))
    session.clear()
    session["user_id"] = str(uid)
    return redirect("/")


@bp.post("/auth/logout")
def logout():
    session.clear()
    return {"ok": True}


@bp.post("/auth/dev-login")
def dev_login():
    cfg = _cfg()
    if not cfg.dev_login_enabled or cfg.app_env == "aws":
        return jsonify(error={"code": "NOT_FOUND", "message": "Not available"}), 404
    email = (request.get_json(silent=True) or {}).get("email", "dev@neurolens.local").strip().lower()
    with db.transaction() as conn:
        uid = upsert_user(conn, email=email, google_sub=None, name=email.split("@")[0], avatar_url=None)
    session.clear()
    session["user_id"] = str(uid)
    return {"ok": True, "user_id": str(uid)}
