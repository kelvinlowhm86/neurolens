"""Flask routes. The web tier never runs the model: analysis happens in the worker.

From M3a the database owns users, credit and job state (docs/M3a_spec.md §6): presign reserves
credit before handing out the upload form, and status and result answer only the job's owner,
checked in the database before any S3 request. From M3b (docs/M3b_spec.md §2-§4, §7b): Cognito
sign-in on AWS, every /api/* route needs a signed-in user, POSTs take JSON only, no CORS, job
history and CSV export, uploads refused while GPU work is paused, Stripe test-mode top-ups.
"""

import logging
import math
import re
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, redirect, request, send_from_directory, session
from werkzeug.exceptions import HTTPException

from neurolens import billing, pricing, results, settings, storage
from neurolens import db as dbmod
from neurolens.web import auth

logger = logging.getLogger("neurolens")

DEFAULT_STARTER_CENTS = 500  # M3a §3b
MIN_DURATION_SECONDS = 1  # a zero-length job would cost nothing yet start a GPU
MAX_FILENAME_CHARS = 255  # the filename is display only
OPEN = (billing.QUEUED, billing.PROCESSING)
WEB_RESUME_WAIT_S = 45  # under the Lambda's and CloudFront's 60 s timeouts (M3b §2c)
RESULT_DAYS = 30  # the results/ lifecycle rule (Terraform)
HISTORY_LIMIT, HISTORY_MAX = 50, 100
STRIPE_PARAMETERS = {
    "secret_key": "/neurolens/web/stripe_secret_key",
    "webhook_secret": "/neurolens/web/stripe_webhook_secret",
    "allowlist": "/neurolens/web/topup_allowlist",
}


def _is_number(value):
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _error(code, message, status=400, **extra):
    return jsonify({"error": code, "message": message, **extra}), status


def _not_configured():
    return _error(
        "not_configured", "The server is missing its database or storage settings.", status=500
    )


def _not_found():
    return _error("not_found", "No such job.", status=404)


def allowlist_emails(text):
    """The top-up allowlist parameter as a set: emails trimmed and lowercased (M3b §7b)."""
    return {email.strip().lower() for email in text.split(",") if email.strip()}


def _stripe_settings(cfg, auth_cfg, ssm_client):
    """Stripe's packs and secrets when test top-ups are on (Cognito mode and stripe.enabled),
    else None. Refuses anything but a test-mode secret key, at startup."""
    stripe_cfg = cfg.get("stripe") or {}
    if auth_cfg["mode"] != "cognito" or not stripe_cfg.get("enabled"):
        return None
    get = settings.get_parameter
    found = {key: get(ssm_client, name) for key, name in STRIPE_PARAMETERS.items()}
    if not found["secret_key"].startswith("sk_test_"):
        raise settings.UnsafeConfigError(
            "The Stripe secret key must be a test-mode key (sk_test_...): the model's licence "
            "forbids commercial use, so NeuroLens never takes real payments."
        )
    packs = stripe_cfg.get("packs") or {}
    if not packs:
        raise ValueError("Missing required setting: stripe.packs")
    return {**found, "allowlist": allowlist_emails(found["allowlist"]), "packs": dict(packs)}


def _worker_group_paused(autoscaling, group_name):
    """True when the worker group's maximum is 0 (stop_work.sh or the breaker), so no upload
    could ever run. A missing group counts as paused. Raises if the group cannot be read."""
    groups = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[group_name])
    found = groups["AutoScalingGroups"]
    return not found or found[0]["MaxSize"] == 0


def _s3_client(cfg):
    """The S3 client from the settings' aws block (which .env fills in), or None without one."""
    if cfg is None or "aws" not in cfg:
        return None
    import boto3

    region = cfg["aws"].get("region")
    if not region:
        raise ValueError(
            "Missing required setting: aws.region (NEUROLENS_AWS_REGION). Set it in .env, "
            "copied from `terraform output region`."
        )
    session = boto3.Session(region_name=region)
    # Fail at start, not at the first upload: signing the upload form needs credentials.
    if session.get_credentials() is None:
        raise ValueError(
            "No AWS credentials found. Set AWS_PROFILE=neurolens in .env (see .env.example) "
            "or in the shell that starts the web app."
        )
    return session.client("s3")


def create_app(*, cfg=None, data_dir=None, db=None, s3_client=None, ssm_client=None):
    """Build the Flask app.

    Never reads config.json itself (the launchers app.py and lambda_handler.py load it and pass
    cfg), so tests need no config file. Without cfg: the built-in development user, max duration
    120, and no S3 client or database unless handed in. A route that needs a database or S3
    client the app doesn't have answers 500 `not_configured`. In Cognito mode every required
    setting and Parameter Store secret is read now, so a missing one fails at startup.
    """
    auth_cfg = auth.auth_settings(cfg)  # first: refuses the dev identity on a public address
    cfg = cfg or {}
    cognito = auth_cfg["mode"] == "cognito"
    worker_group = (cfg.get("aws") or {}).get("worker_group")
    if cognito and not worker_group:
        raise ValueError("Missing required setting in Cognito mode: aws.worker_group")
    region = (cfg.get("aws") or {}).get("region")
    if cognito and ssm_client is None:
        import boto3

        ssm_client = boto3.client("ssm", region_name=region)

    root = settings.get_root()
    if data_dir is None:
        data_dir = settings.resolve_paths(cfg, root)["data"] if "paths" in cfg else root / "data"
    data_dir = Path(data_dir)

    s3 = s3_client if s3_client is not None else _s3_client(cfg or None)
    if db is None and cfg.get("db"):
        db = dbmod.from_config(cfg, resume_wait_s=WEB_RESUME_WAIT_S)
    bucket = (cfg.get("aws") or {}).get("s3_bucket")
    starter_cents = (cfg.get("billing") or {}).get("starter_cents", DEFAULT_STARTER_CENTS)
    max_bytes = cfg.get("max_upload_bytes")
    max_seconds = settings.max_duration(cfg or None)
    static_dir = root / "static"
    autoscaling = None
    if worker_group:
        import boto3

        autoscaling = boto3.client("autoscaling", region_name=region)

    # Users this process has already made sure of, so a page costs no database write each time.
    known_users = set()
    known_lock = threading.Lock()

    app = Flask(__name__, static_folder=str(static_dir))
    app.config["MAX_DURATION"] = max_seconds
    app.config["NEUROLENS_AUTH"] = auth_cfg
    oauth = auth.init_auth(app, cfg, ssm_client)
    stripe_cfg = _stripe_settings(cfg, auth_cfg, ssm_client)

    @app.errorhandler(Exception)
    def unexpected(err):
        if isinstance(err, HTTPException):
            return err
        logger.exception("Unexpected error")
        return _error("server_error", "Something went wrong. Please try again.", status=500)

    @app.errorhandler(dbmod.DatabaseWaking)
    def database_waking(err):
        logger.warning(f"Database still waking: {err}")
        return _error("database_waking", "Starting up. Please try again shortly.", status=503)

    @app.before_request
    def api_rules():
        """Every /api/* route: a signed-in user, and JSON for POSTs (cross-site forms cannot send
        it without a CORS check this app never grants)."""
        if not request.path.startswith("/api/"):
            return None
        if auth.current_user() is None:
            return _error("not_signed_in", "Please sign in.", status=401)
        if request.method == "POST" and not request.is_json:
            return _error("json_required", "The request body must be JSON.", status=415)
        return None

    def signed_in_user():
        """(user_id, email) of the caller. Dev mode creates its user with the starter credit on
        first sight; a Cognito user was created at sign-in."""
        user_id, email = auth.current_user()
        if cognito:
            return user_id, email
        with known_lock:
            known = user_id in known_users
        if not known:
            billing.ensure_user(db, user_id, email, starter_cents)
            with known_lock:
                known_users.add(user_id)
        return user_id, email

    def can_top_up(email):
        return stripe_cfg is not None and email.strip().lower() in stripe_cfg["allowlist"]

    @app.route("/")
    def index():
        return send_from_directory(str(static_dir), "index.html")

    @app.route("/healthz")
    def healthz():
        return jsonify({"ok": True})

    @app.route("/data/<path:filename>")
    def serve_data(filename):
        """Video files, thumbnails and samples.json (laptop; on AWS they come from S3)."""
        return send_from_directory(str(data_dir), filename)

    if cognito:

        @app.route("/login")
        def login():
            return oauth.authorize_redirect(f"{auth_cfg['public_base_url']}/auth/callback")

        @app.route("/auth/callback")
        def auth_callback():
            try:
                token = oauth.authorize_access_token()
                claims = token["userinfo"]
            except Exception as err:  # a bad state, token, signature or a cancelled sign-in
                logger.warning(f"Sign-in failed: {type(err).__name__}: {err}")
                return _error("sign_in_failed", "Sign-in failed. Please try again.")
            if not auth.email_is_verified(claims):
                return _error(
                    "email_not_verified", "Please verify your email address first.", status=403
                )
            email = claims.get("email")
            if not isinstance(email, str) or not email:
                return _error("sign_in_failed", "Sign-in failed. Please try again.")
            user_id = billing.sign_in(db, claims["iss"], claims["sub"], email, starter_cents)
            auth.start_session(user_id, email)
            return redirect("/")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return jsonify({"logout_url": auth.logout_url(auth_cfg) if cognito else "/"})

    @app.route("/api/limits")
    def get_limits():
        """The upload limits, so the page holds no copy of them (config.json is the one place)."""
        return jsonify({"max_video_duration_seconds": max_seconds, "max_upload_bytes": max_bytes})

    @app.route("/api/me")
    def me():
        if db is None:
            return _not_configured()
        user_id, email = signed_in_user()
        return jsonify(
            {
                "user_id": user_id,
                "email": email,
                "balance": billing.get_balance(db, user_id),
                "can_top_up": can_top_up(email),
            }
        )

    @app.route("/api/uploads/presign", methods=["POST"])
    def presign():
        """Reserve the estimated price, then give the browser a one-off signed form to upload
        the video straight to S3."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error("invalid_request", "The request body must be a JSON object.")

        content_type = data.get("content_type")
        if content_type not in storage.CONTENT_TYPE_EXTENSIONS:
            return _error(
                "unsupported_content_type",
                "Only MP4, MOV or WebM videos are accepted.",
            )

        # UX checks only: the browser's numbers can be wrong or spoofed. The real checks are
        # S3's size cap and the worker's head_object / ffprobe checks (which re-price the job).
        duration = data.get("client_duration_seconds")
        if not _is_number(duration) or duration < MIN_DURATION_SECONDS:
            return _error(
                "invalid_request",
                f"client_duration_seconds must be a number of at least {MIN_DURATION_SECONDS}.",
            )
        if duration > max_seconds:
            return _error(
                "duration_exceeds_max_estimated",
                f"Video is too long. The maximum is {max_seconds} seconds.",
                max_seconds=max_seconds,
            )

        declared = data.get("client_declared_bytes")
        if max_bytes is not None and _is_number(declared) and declared > max_bytes:
            return _error(
                "file_too_large",
                f"File is too large. The maximum is {max_bytes} bytes.",
                max_bytes=max_bytes,
            )

        if db is None or s3 is None or not bucket:
            return _not_configured()
        if max_bytes is None:
            # A missing limit must never mean "unlimited". Checked before reserving any credit.
            logger.error("Presign refused: config.json has no max_upload_bytes")
            return _error("presign_failed", "Could not prepare the upload.", status=500)

        if autoscaling is not None:
            # Before any reservation: while GPU work is stopped no upload could run (§4a).
            try:
                paused = _worker_group_paused(autoscaling, worker_group)
            except Exception:
                logger.exception("Presign refused: could not read the worker group")
                return _error("presign_failed", "Could not prepare the upload.", status=500)
            if paused:
                return _error(
                    "processing_paused",
                    "Processing is paused right now, so no video can be analysed.",
                    status=503,
                )

        user_id, _ = signed_in_user()
        job_id = str(uuid.uuid4())
        key = storage.object_key(user_id, job_id, content_type)
        filename = data.get("filename")
        filename = filename[:MAX_FILENAME_CHARS] if isinstance(filename, str) else None
        try:
            reserved = billing.reserve(db, user_id, job_id, key, filename, round(duration * 1000))
        except billing.InsufficientCredit as err:
            return _error(
                "insufficient_credit",
                "Not enough credit for this video.",
                status=402,
                available_cents=err.available_cents,
                required_cents=err.required_cents,
            )

        try:
            presigned = storage.presign_upload(s3, bucket, key, max_bytes)
        except Exception:
            logger.exception(f"Presign failed for {job_id}; refunding its reservation")
            billing.issue_refund(db, job_id, "presign_failed", queued_before_s=0)
            return _error("presign_failed", "Could not prepare the upload.", status=500)

        return jsonify(
            {
                "job_id": job_id,
                "object_key": key,
                **presigned,
                "estimated_cost_usd": pricing.estimate_cost_usd(duration),
                "reserved_cents": reserved,
            }
        )

    @app.route("/api/jobs")
    def list_jobs():
        """The caller's jobs, newest first (M3b §4a)."""
        limit = request.args.get("limit", str(HISTORY_LIMIT))
        if not re.fullmatch(r"\d+", limit) or not 1 <= int(limit) <= HISTORY_MAX:
            return _error("bad_limit", f"limit must be a whole number from 1 to {HISTORY_MAX}.")
        if db is None:
            return _not_configured()
        user_id, _ = signed_in_user()
        cutoff = datetime.now(UTC) - timedelta(days=RESULT_DAYS)
        jobs = billing.list_jobs(db, user_id, int(limit))
        return jsonify(
            [
                {
                    **job,
                    "job_id": str(job["job_id"]),
                    "created_at": storage.utc_text(job["created_at"]),
                    "result_available": job["status"] == billing.DONE
                    and job["created_at"] > cutoff,
                }
                for job in jobs
            ]
        )

    def owned_job(job_id):
        """(job, None) for the caller's own job, else (None, an error response). Checked in the
        database before any S3 request, so a job's existence never leaks to another user."""
        try:
            job_id = str(uuid.UUID(job_id))
        except ValueError:
            return None, _error("bad_job_id", "The job id is not valid.")
        if db is None:
            return None, _not_configured()
        user_id, _ = signed_in_user()
        with db.transaction() as tx:
            rows = tx.execute(
                "SELECT job_id, status, stage, stages, attempt, updated_at, error_code, "
                "error_message FROM jobs WHERE job_id = CAST(:job_id AS uuid) "
                "AND user_id = :user_id",
                {"job_id": job_id, "user_id": user_id},
            )
        if not rows:
            return None, _not_found()
        return rows[0], None

    def owned_result(job_id):
        """(result, None) for the caller's job, else (None, an error response). Served for a
        done job and for an unfinished one whose result exists (M3a's crash window: the worker
        wrote it, then died before settling). A refunded job never serves one, even one a late
        worker wrote."""
        job, error = owned_job(job_id)
        if error:
            return None, error
        if job["status"] == billing.FAILED:
            return None, _not_found()
        if s3 is None or not bucket:
            return None, _not_configured()
        result = storage.get_result(s3, bucket, job["job_id"])
        if result is not None:
            return result, None
        if job["status"] == billing.DONE:
            return None, _error(
                "result_expired", f"Results are kept {RESULT_DAYS} days.", status=404
            )
        return None, _error("result_not_ready", "The result is not ready yet.", status=404)

    @app.route("/api/jobs/<job_id>/status")
    def job_status(job_id):
        """The job as the database has it, except that an unfinished job whose result exists is
        `done` (a worker crashed between writing the result and settling)."""
        job, error = owned_job(job_id)
        if error:
            return error
        status = job["status"]
        if status in OPEN:
            if s3 is None or not bucket:
                return _not_configured()
            if storage.result_exists(s3, bucket, job["job_id"]):
                status = billing.DONE
        return jsonify({**job, "status": status, "updated_at": storage.utc_text(job["updated_at"])})

    @app.route("/api/jobs/<job_id>/result")
    def job_result(job_id):
        result, error = owned_result(job_id)
        return error or jsonify(result)

    @app.route("/api/jobs/<job_id>/result.csv")
    def job_result_csv(job_id):
        result, error = owned_result(job_id)
        if error:
            return error
        name = f"neurolens-{uuid.UUID(job_id)}.csv"
        return (
            results.to_csv(result),
            200,
            {
                "Content-Type": "text/csv; charset=utf-8",
                "Content-Disposition": f'attachment; filename="{name}"',
            },
        )

    @app.route("/api/top-up/checkout", methods=["POST"])
    def top_up_checkout():
        """A Stripe Checkout Session for a server-defined pack (M3b §7b). The browser sends only
        the pack's name: never an amount, user or session."""
        if stripe_cfg is None:
            return _not_found()
        user_id, email = signed_in_user()
        if not can_top_up(email):
            return _error("top_up_not_allowed", "Top-ups are for the NeuroLens team.", status=403)
        data = request.get_json(silent=True)
        pack = data.get("pack") if isinstance(data, dict) else None
        if not isinstance(pack, str) or pack not in stripe_cfg["packs"]:
            return _error("unknown_pack", "Unknown top-up pack.")
        import stripe

        base = auth_cfg["public_base_url"]
        checkout = stripe.checkout.Session.create(
            api_key=stripe_cfg["secret_key"],
            mode="payment",
            # We are the seller and this is test credit, not a sale through Stripe as merchant of
            # record: Managed Payments off whatever the account's default (it refuses card-only and
            # requires tax codes), so card only can be named here.
            managed_payments={"enabled": False},
            payment_method_types=["card"],
            line_items=[
                {
                    "quantity": 1,
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": stripe_cfg["packs"][pack],
                        "product_data": {"name": f"NeuroLens test credit (pack {pack})"},
                    },
                }
            ],
            metadata={"user_id": user_id, "pack": pack},
            success_url=f"{base}/?top_up=done",
            cancel_url=f"{base}/?top_up=cancelled",
        )
        return jsonify({"url": checkout.url})

    return app
