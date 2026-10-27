from flask import Blueprint, current_app

from neurolens_common import db

from .auth import current_user_id, login_required

bp = Blueprint("me", __name__)


@bp.get("/api/me")
@login_required
def me():
    cfg = current_app.extensions["nl_cfg"]
    with db.transaction() as conn:
        user = conn.execute(
            """SELECT id, email, name, avatar_url, credits_available, credits_reserved, stripe_customer_id
                 FROM users WHERE id = %s""",
            (current_user_id(),),
        ).fetchone()
    if user is None:
        return {"error": {"code": "UNAUTHENTICATED", "message": "Sign in again"}}, 401
    has_sub = user.pop("stripe_customer_id") is not None
    return {
        "user": user,
        "has_subscription": has_sub,
        "pricing": {
            "credits_per_second": cfg.credits_per_second,
            "min_charge_credits": cfg.min_charge_credits,
            "max_video_seconds": cfg.max_video_seconds,
            "max_upload_bytes": cfg.max_upload_bytes,
            "allowed_types": cfg.allowed_types,
        },
    }


@bp.get("/api/config")
def public_config():
    """What the signed-out page needs to decide which buttons to show."""
    cfg = current_app.extensions["nl_cfg"]
    return {
        "google_login": bool(cfg.google_client_id),
        "dev_login": cfg.dev_login_enabled and cfg.app_env != "aws",
        "billing": bool(cfg.stripe_secret_key),
        "max_video_seconds": cfg.max_video_seconds,
    }
