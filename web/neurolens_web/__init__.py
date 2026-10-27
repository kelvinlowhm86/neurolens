"""
NeuroLens web tier: stateless Flask app behind CloudFront + ALB.

It never touches video bytes. It authenticates, reserves credits, mints presigned S3 upload URLs,
nudges the GPU pool awake, and serves job status / results / billing. Session state lives in a
signed cookie, so any task behind the ALB can serve any request.

    gunicorn "neurolens_web:create_app()"
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from flask import Flask, send_from_directory
from flask.json.provider import DefaultJSONProvider

from neurolens_common import migrate
from neurolens_common.config import Settings, get_settings

from .services import Services


class _JSON(DefaultJSONProvider):
    """ISO-8601 dates, UUIDs as strings, Decimals as floats."""

    @staticmethod
    def default(o):
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        if isinstance(o, UUID):
            return str(o)
        if isinstance(o, Decimal):
            return float(o)
        return DefaultJSONProvider.default(o)


def create_app(cfg: Settings | None = None, services: Services | None = None, run_migrations: bool = True) -> Flask:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    cfg = cfg or get_settings()
    app = Flask(__name__, static_folder=None)
    app.json = _JSON(app)
    app.config.update(
        SECRET_KEY=cfg.flask_secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=cfg.public_base_url.startswith("https://"),
        MAX_CONTENT_LENGTH=1024 * 1024,      # API bodies only — videos go straight to S3
    )
    app.extensions["nl_cfg"] = cfg
    app.extensions["nl"] = services or Services.from_settings(cfg)

    if run_migrations:
        migrate.apply(cfg.database_url, cfg.migrations_dir)

    from . import api_billing, api_jobs, api_me, api_samples, auth

    auth.init_oauth(app, cfg)
    for bp in (auth.bp, api_me.bp, api_jobs.bp, api_billing.bp, api_samples.bp):
        app.register_blueprint(bp)

    static_dir = cfg.static_dir.resolve()

    @app.get("/healthz")
    def healthz():
        # Deliberately no DB call: ALB health checks must not keep Aurora awake.
        return {"ok": True}

    @app.get("/")
    def index():
        return send_from_directory(static_dir, "index.html")

    @app.get("/static/<path:filename>")
    def static_files(filename):
        return send_from_directory(static_dir, filename)

    return app
