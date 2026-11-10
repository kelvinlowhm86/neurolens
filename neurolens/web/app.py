"""Flask routes. The web tier never runs the model: analysis happens in the worker.

From M3a the database owns users, credit and job state (docs/M3a_spec.md §6): presign reserves
credit before handing out the upload form, and status and result answer only the job's owner,
checked in the database before any S3 request. From M3b (docs/M3b_spec.md §2-§4, §7b): Cognito
sign-in on AWS, every /api/* route needs a signed-in user, POSTs take JSON only, no CORS, job
history and CSV export, uploads refused while GPU work is paused.
"""

import hashlib
import hmac
import json
import logging
import math
import re
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from flask import Flask, jsonify, redirect, request, send_from_directory, session
from werkzeug.exceptions import HTTPException

from neurolens import (
    access,
    billing,
    pricing,
    qr,
    report_prompt,
    results,
    scaling,
    settings,
    storage,
    youtube,
)
from neurolens import db as dbmod
from neurolens import (
    notify as notify_module,
)
from neurolens.web import auth

logger = logging.getLogger("neurolens")

DEFAULT_STARTER_CENTS = 500  # M3a §3b
MIN_DURATION_SECONDS = 1  # a zero-length job would cost nothing yet start a GPU
MAX_FILENAME_CHARS = 255  # the filename is display only
OPEN = (billing.QUEUED, billing.PROCESSING)
WEB_RESUME_WAIT_S = 45  # under the Lambda's and CloudFront's 60 s timeouts (M3b §2c)
RESULT_DAYS = 30  # the results/ lifecycle rule (Terraform)
VIDEO_LINK_SECONDS = 3600  # a video is streamed in pieces; one hour outlives a paused viewer
HISTORY_LIMIT, HISTORY_MAX = 50, 100
RETENTION_MAX_BYTES = 64 * 1024
RETENTION_VIEWS = {"lt_1k", "1k_10k", "10k_100k", "gt_100k"}
RETENTION_PUBLISHED = {"week", "month", "older"}
DEFAULT_REWARD_CAP = 5
DEFAULT_REWARD_WINDOW_DAYS = 30
JOB_ACTIVE_MESSAGE = "This video is still waiting or being processed. Try again when it finishes."
# SHA-256 hex digests of lowercased, stripped booth-admin emails (so raw emails never sit in git).
# Add one with: python3 -c 'import hashlib; print(hashlib.sha256(b"you@example.com").hexdigest())'
BOOTH_ADMIN_HASHES = frozenset(
    {
        "1913f1aa43f6f0cbfdedd18e5842e072a830000008ba7e1191c682a900c2a852",
        "0326082fc86908b10234f55a7b34359a883f16e2b3701b2da1124ac2dd933754",
        "29b946a2e02413f159b8db47288f2f8813de733828476bfdbb0dc13c4cc9284f",
    }
)
BOOTH_ADJUST_DELTAS = (0, 5, 10)


def is_booth_admin(email):
    if not isinstance(email, str) or not email:
        return False
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
    return digest in BOOTH_ADMIN_HASHES


def _is_number(value):
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _is_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _error(code, message, status=400, **extra):
    return jsonify({"error": code, "message": message, **extra}), status


def _stem(filename):
    """The upload's file name without its extension, or "" when there is none."""
    return PurePosixPath(filename).stem.strip() if isinstance(filename, str) else ""


def _disposition(stem, suffix="NeuroLens.csv", fallback="NeuroLens result.csv"):
    """The attachment name, "<stem> – <suffix>" (M3f §4.7, M3h §18b). HTTP headers are latin-1,
    so the full name goes in RFC 5987's `filename*`; `filename` carries a plain-ASCII fallback."""
    stem = stem.strip()
    if not stem:
        return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(fallback, safe='')}"
    ascii_stem = "".join(
        c if c.isascii() and c.isprintable() and c not in '"\\' else "_" for c in stem
    )
    full = quote(f"{stem} \u2013 {suffix}", safe="")
    return f"attachment; filename=\"{ascii_stem} - {suffix}\"; filename*=UTF-8''{full}"


def _not_configured():
    return _error(
        "not_configured", "The server is missing its database or storage settings.", status=500
    )


def _not_found():
    return _error("not_found", "No such job.", status=404)


def _job_active():
    return _error("job_active", JOB_ACTIVE_MESSAGE, status=409)


def _old_result():
    return _error(
        "old_result",
        "This result was made by an older version of NeuroLens. Upload the video again.",
        status=410,
    )


def _not_signed_in():
    """The one not-signed-in answer: clear the session and its hint cookie (M3c §5)."""
    session.clear()
    response, status = _error("not_signed_in", "Please sign in.", status=401)
    return auth.clear_signed_in_hint(response), status


def _delete_curve_files(s3, bucket, keys, job_id):
    """Remove the two snapshot files a refused curve upload wrote (M3f §5d)."""
    try:
        if not storage.delete_keys(s3, bucket, keys):
            logger.error(f"S3 reported failed deletes for the refused curve of {job_id}")
    except Exception:
        logger.exception(f"Could not delete the refused curve files of {job_id}")


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


def create_app(
    *,
    cfg=None,
    data_dir=None,
    db=None,
    s3_client=None,
    ssm_client=None,
    report_starter=None,
    notify=None,
):
    """Build the Flask app.

    Never reads config.json itself (the launchers app.py and lambda_handler.py load it and pass
    cfg), so tests need no config file. Without cfg: the built-in development user, max duration
    120, and no S3 client or database unless handed in. A route that needs a database or S3
    client the app doesn't have answers 500 `not_configured`. In Cognito mode every required
    setting and Parameter Store secret is read now, so a missing one fails at startup.
    """
    auth_cfg = auth.auth_settings(cfg)  # first: refuses the dev identity on a public address
    cfg = cfg or {}
    response_cfg = cfg.get("response")
    if cfg:  # a launcher's settings must carry a usable block: fail at start-up, not per request
        results.validate_response_config(response_cfg)
    cognito = auth_cfg["mode"] == "cognito"
    gpu_endpoint = (cfg.get("aws") or {}).get("gpu_endpoint")
    if cognito and not gpu_endpoint:
        raise ValueError("Missing required setting in Cognito mode: aws.gpu_endpoint")
    region = (cfg.get("aws") or {}).get("region")
    if notify is None:
        topic_arn = cfg.get("request_topic_arn")
        if cognito:
            if not topic_arn:
                raise ValueError("Missing required setting in Cognito mode: request_topic_arn")
            notify = notify_module.sns_notifier(region, topic_arn)
        else:

            def notify(request, email):
                logger.info(
                    f"access request from user {request['user_id']} (not emailed: dev mode)"
                )

    if cognito and ssm_client is None:
        import boto3

        ssm_client = boto3.client("ssm", region_name=region)
    report_function = (cfg.get("aws") or {}).get("report_function")
    if report_starter is None and cognito and report_function:
        import boto3

        lambda_client = boto3.client("lambda", region_name=region)

        def report_starter(event):
            lambda_client.invoke(
                FunctionName=report_function,
                InvocationType="Event",
                Payload=json.dumps(event).encode(),
            )

    root = settings.get_root()
    if data_dir is None:
        data_dir = settings.resolve_paths(cfg, root)["data"] if "paths" in cfg else root / "data"
    data_dir = Path(data_dir)

    s3 = s3_client if s3_client is not None else _s3_client(cfg or None)
    if db is None and cfg.get("db"):
        db = dbmod.from_config(cfg, resume_wait_s=WEB_RESUME_WAIT_S)
    bucket = (cfg.get("aws") or {}).get("s3_bucket")
    starter_cents = (cfg.get("billing") or {}).get("starter_cents", DEFAULT_STARTER_CENTS)
    invite_cents = (cfg.get("billing") or {}).get("invite_cents", 0)
    max_bytes = cfg.get("max_upload_bytes")
    max_seconds = settings.max_duration(cfg or None)
    upload_cfg = cfg.get("upload") or {}
    max_batch_files = upload_cfg.get("max_batch_files")
    max_waiting_jobs = upload_cfg.get("max_waiting_jobs")
    research_cfg = cfg.get("research") or {}
    reward_cap = research_cfg.get("curve_rewards_per_window", DEFAULT_REWARD_CAP)
    reward_window_days = research_cfg.get("curve_reward_window_days", DEFAULT_REWARD_WINDOW_DAYS)
    length_tolerance_s = research_cfg.get(
        "curve_length_tolerance_s", billing.DEFAULT_LENGTH_TOLERANCE_S
    )
    curve_min_views = research_cfg.get("curve_min_views", billing.DEFAULT_MIN_VIEWS)
    static_dir = root / "static"
    autoscaling = None
    sagemaker = None
    if gpu_endpoint:
        import boto3

        autoscaling = boto3.client("application-autoscaling", region_name=region)
        sagemaker = boto3.client("sagemaker", region_name=region)
    cognito_idp = None
    user_pool_id = None
    if cognito:
        import boto3

        cognito_idp = boto3.client("cognito-idp", region_name=region)
        user_pool_id = auth_cfg["cognito_issuer"].rstrip("/").rsplit("/", 1)[-1]

    # Users this process has already made sure of, so a page costs no database write each time.
    known_users = set()
    known_lock = threading.Lock()

    app = Flask(__name__, static_folder=str(static_dir))
    app.config["MAX_DURATION"] = max_seconds
    app.config["NEUROLENS_AUTH"] = auth_cfg
    app.config["NEUROLENS_DB"] = db
    oauth = auth.init_auth(app, cfg, ssm_client)

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
        """Every /api/* route except GET /api/limits: JSON for POSTs (cross-site forms cannot send
        it without a CORS check this app never grants), and a signed-in user except for POST
        /api/wake. The limits are public so signed-out visitors' sample brains can be coloured
        (M3d §6); the wake so the landing page can start Aurora's resume before sign-in."""
        if not request.path.startswith("/api/"):
            return None
        if request.path == "/api/limits" and request.method == "GET":
            return None
        public = request.path == "/api/wake" and request.method == "POST"
        if not public and auth.current_user() is None:
            return _not_signed_in()
        if request.method == "POST" and request.content_length and not request.is_json:
            return _error("json_required", "The request body must be JSON.", status=415)
        return None

    def signed_in_user():
        """(user_id, email) of the caller. Dev mode creates its user with the starter credit on
        first sight; a Cognito user or booth guest was created at sign-in / pass redemption."""
        user_id, email = auth.current_user()
        if cognito or billing.is_booth_guest(user_id):
            return user_id, email
        with known_lock:
            known = user_id in known_users
        if not known:
            billing.ensure_user(db, user_id, email, starter_cents)
            with known_lock:
                known_users.add(user_id)
        return user_id, email

    def _sign_booth_pass(pass_id):
        key = str(app.secret_key).encode("utf-8")
        sig = hmac.new(key, pass_id.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
        return f"{pass_id}.{sig}"

    def _verify_booth_pass(token):
        if not isinstance(token, str) or "." not in token:
            return None
        pass_id, sig = token.rsplit(".", 1)
        if not _is_uuid(pass_id):
            return None
        key = str(app.secret_key).encode("utf-8")
        expected = hmac.new(key, pass_id.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected):
            return None
        return pass_id.lower()

    @app.route("/")
    def index():
        page = send_from_directory(str(static_dir), "index.html")
        # A laptop is always signed in as the dev user: show the signed-in buttons.
        return page if cognito else auth.set_signed_in_hint(page)

    @app.route("/app")
    def app_page():
        page = send_from_directory(str(static_dir), "app.html")
        # The landing page's hint follows the sliding session (M3f §6b).
        if cognito and auth.current_user() is not None:
            return auth.set_signed_in_hint(page)
        return page

    @app.route("/healthz")
    def healthz():
        return jsonify({"ok": True})

    @app.route("/data/<path:filename>")
    def serve_data(filename):
        """Video files, thumbnails and samples.json (laptop; on AWS they come from S3)."""
        return send_from_directory(str(data_dir), filename)

    if not cognito:

        @app.route("/login")
        def dev_login():
            """A laptop is always signed in as the dev user: Sign in just opens the app."""
            return redirect("/app")

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
            auth.start_session(db, user_id)
            return auth.set_signed_in_hint(redirect("/app"))

    @app.route("/logout", methods=["POST"])
    def logout():
        caller = auth.current_user()
        is_guest = caller is not None and billing.is_booth_guest(caller[0])
        auth.end_session(db)
        return auth.clear_signed_in_hint(
            jsonify(
                {"logout_url": auth.logout_url(auth_cfg) if (cognito and not is_guest) else "/"}
            )
        )

    @app.route("/api/limits")
    def get_limits():
        """The upload limits and the price, so the page holds no copy of either (config.json and
        neurolens.pricing are the one place)."""
        limits = {
            "max_video_duration_seconds": max_seconds,
            "max_upload_bytes": max_bytes,
            "colour_range": (response_cfg or {}).get("colour_range"),
            "invite_seconds": invite_cents * pricing.BLOCK_SECONDS / pricing.CENTS_PER_BLOCK,
            "price": {
                "cents_per_block": pricing.CENTS_PER_BLOCK,
                "block_seconds": pricing.BLOCK_SECONDS,
                "allowance_seconds": pricing.ALLOWANCE_SECONDS,
            },
        }
        if max_batch_files is not None:
            limits["max_batch_files"] = max_batch_files
        return jsonify(limits)

    def _gpu_status():
        if sagemaker is None or not gpu_endpoint:
            return {"status": "local", "current": 0, "desired": 0}
        try:
            desc = sagemaker.describe_endpoint(EndpointName=gpu_endpoint)
            variants = desc.get("ProductionVariants") or []
            v = variants[0] if variants else {}
            return {
                "status": desc.get("EndpointStatus", "Unknown"),
                "current": v.get("CurrentInstanceCount", 0),
                "desired": v.get("DesiredInstanceCount", 0),
            }
        except Exception:
            logger.exception("Could not read SageMaker endpoint status")
            return {"status": "unknown", "current": 0, "desired": 0}

    @app.route("/api/wake", methods=["POST"])
    def wake():
        """Start Aurora's resume from the landing page's first interaction, or redeem a booth QR
        pass when `{"booth": token}` is sent."""
        data = request.get_json(silent=True) if request.is_json else None
        if isinstance(data, dict) and "booth" in data:
            if db is None:
                return _not_configured()
            pass_id = _verify_booth_pass(data.get("booth"))
            if pass_id is None:
                return _error("invalid_booth_pass", "This booth QR pass is not valid.", status=400)
            caller = auth.current_user()
            current_uid = caller[0] if caller else None
            try:
                guest_id = billing.redeem_booth_pass(db, pass_id, current_user_id=current_uid)
            except billing.BoothLimitReached:
                return _error(
                    "booth_limit_reached",
                    "All booth passes have been claimed for now. Ask the booth team for more!",
                    status=409,
                )
            except billing.BoothPassEnded:
                return _error(
                    "booth_pass_ended",
                    "This booth session has ended. Scan a new QR code at the booth.",
                    status=410,
                )
            auth.start_session(db, guest_id)
            return auth.set_signed_in_hint(jsonify({"user_id": guest_id}))
        if db is not None:
            try:
                db.wake()
            except Exception:
                logger.exception("Wake failed")
        return "", 204

    @app.route("/api/me")
    def me():
        if db is None:
            return _not_configured()
        user_id, email = signed_in_user()
        payload = {
            "user_id": user_id,
            "email": email,
            "balance": billing.get_balance(db, user_id),
            "access": {
                "funded": billing.is_funded(db, user_id),
                "request": access.request_status(db, user_id),
            },
        }
        if is_booth_admin(email):
            payload["booth_admin"] = True
        return jsonify(payload)

    @app.route("/api/booth/pass", methods=["POST"])
    def booth_pass():
        if db is None:
            return _not_configured()
        _, email = signed_in_user()
        if not is_booth_admin(email):
            return _error("forbidden", "Booth QR is only available to booth admins.", status=403)
        data = request.get_json(silent=True) if request.is_json else None
        prefetch = isinstance(data, dict) and bool(data.get("prefetch"))
        pass_id = str(uuid.uuid4())
        token = _sign_booth_pass(pass_id)
        base_url = auth_cfg.get("public_base_url") or request.host_url.rstrip("/")
        url = f"{base_url}/app?booth={token}"
        payload = {
            "pass_id": pass_id,
            "token": token,
            "url": url,
            "qr_svg": qr.svg(url),
        }
        if not prefetch:
            stats = billing.booth_stats(db)
            if request.args.get("gpu") == "1":
                stats["gpu"] = _gpu_status()
            payload["stats"] = stats
        return jsonify(payload)

    def _sync_gpu_capacity(stats, *, allow_error=False):
        """Reconcile SageMaker's desired capacity with the saved booth warm target (`0`, `1`, `2`)
        without interrupting active jobs or throwing an error while SageMaker is mid-update."""
        gpu = _gpu_status()
        if sagemaker is None or not gpu_endpoint:
            return gpu, None
        warm_target = stats.get("gpu_warm_target", 0)
        videos_running = stats.get("videos_running", 0)
        desired = gpu.get("desired", 0)
        need_update = False
        if warm_target in (1, 2):
            if desired != warm_target:
                need_update = True
        elif warm_target == 0 and desired > 0 and videos_running == 0:
            need_update = True
        if not need_update:
            return gpu, None
        if gpu.get("status") in ("Updating", "Creating", "SystemUpdating"):
            gpu["queued_desired"] = warm_target
            return gpu, None
        try:
            sagemaker.update_endpoint_weights_and_capacities(
                EndpointName=gpu_endpoint,
                DesiredWeightsAndCapacities=[
                    {"VariantName": "main", "DesiredInstanceCount": warm_target}
                ],
            )
            gpu["desired"] = warm_target
            gpu["status"] = "Updating"
        except Exception as err:
            msg = str(err)
            if "in-progress" in msg.lower() or "updating" in msg.lower():
                gpu["status"] = "Updating"
                gpu["queued_desired"] = warm_target
                return gpu, None
            if allow_error:
                return gpu, _error("gpu_update_failed", msg, status=409)
            logger.exception("Could not reconcile booth GPU warm target")
        return gpu, None

    @app.route("/api/booth/stats")
    def booth_stats_route():
        if db is None:
            return _not_configured()
        _, email = signed_in_user()
        if not is_booth_admin(email):
            return _error(
                "forbidden", "Booth stats are only available to booth admins.", status=403
            )
        pass_id = request.args.get("pass_id")
        if pass_id is not None and not _is_uuid(pass_id):
            pass_id = None
        stats = billing.booth_stats(db, check_pass_id=pass_id)
        if request.args.get("gpu") == "1":
            gpu, _ = _sync_gpu_capacity(stats)
            stats["gpu"] = gpu
        return jsonify(stats)

    @app.route("/api/booth/passes", methods=["POST"])
    def booth_passes_adjust():
        if db is None:
            return _not_configured()
        _, email = signed_in_user()
        if not is_booth_admin(email):
            return _error(
                "forbidden", "Booth passes can only be changed by booth admins.", status=403
            )
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error("invalid_request", "The request body must be a JSON object.")
        if "gpu_desired" in data:
            desired = data.get("gpu_desired")
            if type(desired) is not int or desired not in (0, 1, 2):
                return _error("invalid_request", "gpu_desired must be 0, 1 or 2.")
            stats = billing.set_booth_gpu_warm(db, desired)
            gpu, err_resp = _sync_gpu_capacity(stats, allow_error=True)
            if err_resp is not None:
                return err_resp
            stats["gpu"] = gpu
            return jsonify(stats)
        if "grant_email" in data:
            target_email = (data.get("grant_email") or "").strip() or email
            minutes = data.get("minutes")
            if type(minutes) is not int or minutes not in (0, 2, 5, 10):
                return _error("invalid_request", "minutes must be 0, 2, 5 or 10.")
            cents = minutes * 60 * pricing.CENTS_PER_BLOCK // pricing.BLOCK_SECONDS
            try:
                if minutes == 0:
                    billing.reset_credit(db, target_email)
                else:
                    billing.grant_credit(db, target_email, cents)
            except LookupError:
                return _error(
                    "user_not_found",
                    f"No account found for {target_email}. Have them sign in first.",
                    status=404,
                )
            stats = billing.booth_stats(db)
            stats["granted"] = {
                "email": target_email,
                "minutes": minutes,
                "cents": cents,
            }
            return jsonify(stats)
        delta = data.get("delta")
        if type(delta) is not int or delta not in BOOTH_ADJUST_DELTAS:
            return _error("invalid_request", "delta must be 0, 5 or 10.")
        return jsonify(billing.adjust_booth_passes(db, delta))

    @app.route("/api/access-request", methods=["POST"])
    def access_request():
        """Ask for free minutes with a channel link (M3k §6).

        The row is committed before the email is sent, because the table is the list of requests:
        a failed notification is logged without the requester's details and still answers 201.
        """
        if db is None:
            return _not_configured()
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error("invalid_request", "The request body must be a JSON object.")
        user_id, email = signed_in_user()
        try:
            stored = access.add_request(db, user_id, data.get("channel_url"), data.get("note"))
        except ValueError as err:
            return _error("bad_request", str(err))
        except access.AlreadyRequested:
            return _error(
                "already_requested",
                "You have already asked for free minutes.",
                status=409,
            )
        try:
            notify(stored, email)
        except Exception as err:
            logger.error(f"access request email failed for user {user_id}: {type(err).__name__}")
        return jsonify({"request": "waiting"}), 201

    @app.route("/api/uploads/presign", methods=["POST"])
    def presign():
        """Reserve the estimated price, then give the browser a one-off signed form to upload
        the video straight to S3."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error("invalid_request", "The request body must be a JSON object.")

        research_consent = data.get("research_consent", False)
        if not isinstance(research_consent, bool):
            return _error("invalid_request", "research_consent must be true or false.")

        content_type = data.get("content_type")
        if content_type not in storage.CONTENT_TYPE_EXTENSIONS:
            return _error(
                "unsupported_content_type",
                "Only MP4, MOV or WebM videos are accepted.",
            )

        # UX checks only: the browser's numbers can be wrong or spoofed. The real checks are
        # S3's size cap and the dispatcher's head_object / ffprobe checks (which re-price the job).
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
                paused = scaling.endpoint_paused(autoscaling, gpu_endpoint)
            except Exception:
                logger.exception("Presign refused: could not read the GPU endpoint")
                return _error("presign_failed", "Could not prepare the upload.", status=500)
            if paused:
                return _error(
                    "processing_paused",
                    "Processing is paused right now, so no video can be analysed.",
                    status=503,
                )

        user_id, _ = signed_in_user()
        if billing.is_booth_guest(user_id):
            research_consent = False
        if research_consent and data.get("consent_version") != billing.CONSENT_VERSION:
            return _error("consent_outdated", "Please refresh the page.", status=409)
        job_id = str(uuid.uuid4())
        key = storage.object_key(user_id, job_id, content_type)
        filename = data.get("filename")
        filename = filename[:MAX_FILENAME_CHARS] if isinstance(filename, str) else None
        try:
            reserved = billing.reserve(
                db,
                user_id,
                job_id,
                key,
                filename,
                round(duration * 1000),
                research_consent=research_consent,
                max_waiting_jobs=max_waiting_jobs,
            )
        except billing.TooManyWaiting:
            return _error(
                "too_many_waiting",
                "Too many videos waiting. Try again when some finish.",
                status=429,
            )
        except billing.AccountDeleted:
            return _not_signed_in()
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
            billing.issue_refund(db, job_id, "presign_failed", queued=True)
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

        def load_jobs():
            return billing.list_jobs(
                db,
                user_id,
                int(limit),
                reward_cap=reward_cap,
                reward_window_days=reward_window_days,
            )

        jobs = load_jobs()
        refunded = False
        for job in jobs:
            if job["gpu_wait_over"] and refund_gpu_wait(job["job_id"]):
                refunded = True
        if refunded:
            jobs = load_jobs()
        rows = []
        for job in jobs:
            row = {
                key: value
                for key, value in job.items()
                if key not in ("research_consent", "gpu_wait_over")
            }
            row["job_id"] = str(job["job_id"])
            row["created_at"] = storage.utc_text(job["created_at"])
            if job["gpu_wait_until"] is not None:
                row["gpu_wait_until"] = storage.utc_text(job["gpu_wait_until"])
            else:
                row.pop("gpu_wait_until")
            row["result_available"] = job["status"] == billing.DONE and job["created_at"] > cutoff
            rows.append(row)
        return jsonify(rows)

    def refund_gpu_wait(job_id):
        """Refund a job whose GPU wait ran out (M3l §2c) while its owner looks. A failure here
        must not break the answer: it is logged, and the reaper refunds the job later."""
        try:
            return billing.refund_if_gpu_wait_over(db, str(job_id))
        except Exception:
            app.logger.exception(f"{job_id}: could not refund the expired GPU wait")
            return False

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
                "SELECT job_id, object_key, filename, status, stage, stages, attempt, updated_at, "
                "claimed_at, gpu_wait_until, error_code, error_message, research_consent, "
                "(status = :queued AND claimed_at IS NULL AND "
                "gpu_wait_until + make_interval(secs => :grace) < now()) AS gpu_wait_over, "
                "retention_added_at, dataset_removed_at, dataset_removed_reason, "
                "verified_duration_ms, report_state, "
                "report_tries, report_started_at, report_error FROM jobs "
                "WHERE job_id = CAST(:job_id AS uuid) AND user_id = :user_id "
                "AND deleted_at IS NULL",
                {
                    "job_id": job_id,
                    "user_id": user_id,
                    "queued": billing.QUEUED,
                    "grace": billing.GPU_WAIT_GRACE_S,
                },
            )
        if not rows:
            return None, _not_found()
        return rows[0], None

    def shared_job(job_id):
        """The caller's own done, shared job, else the refusal both curve routes give (M3j §6)."""
        job, error = owned_job(job_id)
        if error:
            return None, error
        if job["status"] != billing.DONE:
            return None, _error("job_not_done", "The job is not done yet.", status=409)
        if not job["research_consent"]:
            return None, _error("no_consent", "This video was not shared.", status=409)
        if job["dataset_removed_at"] is not None:
            message = billing.CURVE_REMOVED_MESSAGES.get(
                job["dataset_removed_reason"], billing.CURVE_REMOVED_MESSAGES["no_curve"]
            )
            return None, _error("dataset_removed", message, status=409)
        return job, None

    def video_facts(platform, video_id, job_id):
        """The checked facts for a link, or None when they cannot be checked (M3j §4).

        Facebook supplies none. A YouTube lookup that fails or finds no video is logged; a missing
        video is a 400 on both routes, while every other failure falls back to the analysis date.
        """
        if platform != "youtube":
            return None, None
        try:
            facts = youtube.lookup(video_id, cfg.get("youtube_api_key"))
        except youtube.LookupFailed as err:
            logger.error(f"VIDEO_LOOKUP_FAILED {job_id}: {err}")
            return None, None
        if facts is None:
            return None, _error(
                "video_not_found",
                "We can't see this video. Check the link, or add it once it's public.",
            )
        return facts, None

    def owned_result(job_id):
        """(result, job, None) for the caller's done job, else (None, None, an error response).
        A done job's result is read from S3; any other job answers ``result_not_ready`` without
        touching S3. A refunded job never serves one, even one a late worker wrote, and a done
        job whose result has expired answers ``result_expired``."""
        job, error = owned_job(job_id)
        if error:
            return None, None, error
        if job["status"] == billing.FAILED:
            return None, None, _not_found()
        if job["status"] != billing.DONE:
            return (
                None,
                None,
                _error("result_not_ready", "The result is not ready yet.", status=404),
            )
        if s3 is None or not bucket:
            return None, None, _not_configured()
        result = storage.get_result(s3, bucket, job["job_id"])
        if result is None:
            return (
                None,
                None,
                _error("result_expired", f"Results are kept {RESULT_DAYS} days.", status=404),
            )
        if result.get("format") != 2:
            return None, None, _old_result()
        return result, job, None

    def frames_for(job_id):
        """The stored frames, or [] when the frames object is missing (other S3 errors raise)."""
        try:
            return storage.get_frames(s3, bucket, job_id)[0]
        except Exception as err:
            if storage.is_not_found(err):
                return []
            raise

    @app.route("/api/jobs/<job_id>/status")
    def job_status(job_id):
        """The job as the database has it: the database alone decides its status."""
        job, error = owned_job(job_id)
        if error:
            return error
        if job["gpu_wait_over"] and refund_gpu_wait(job_id):
            job, error = owned_job(job_id)
            if error:
                return error
        # Consent gates the retention upload, which needs a done job, so it is reported only then.
        hidden = {
            "object_key",
            "claimed_at",
            "gpu_wait_until",
            "gpu_wait_over",
            "retention_added_at",
            "dataset_removed_at",
            "dataset_removed_reason",
            "verified_duration_ms",
            "report_state",
            "report_tries",
            "report_started_at",
            "report_error",
        }
        public = {
            key: value
            for key, value in job.items()
            if key not in hidden and (key != "research_consent" or job["status"] == billing.DONE)
        }
        answer = {
            **public,
            "cancellable": job["status"] == billing.QUEUED and job["verified_duration_ms"] is None,
            "updated_at": storage.utc_text(job["updated_at"]),
        }
        if job["gpu_wait_until"] is not None:
            answer["gpu_wait_until"] = storage.utc_text(job["gpu_wait_until"])
        return jsonify(answer)

    @app.route("/api/jobs/<job_id>/result")
    def job_result(job_id):
        result, _, error = owned_result(job_id)
        if error:
            return error
        return jsonify({**result, "summary": results.summarise(result)})

    @app.route("/api/jobs/<job_id>/result.csv")
    def job_result_csv(job_id):
        result, job, error = owned_result(job_id)
        if error:
            return error
        return (
            results.to_csv(result),
            200,
            {
                "Content-Type": "text/csv; charset=utf-8",
                "Content-Disposition": _disposition(_stem(job["filename"])),
            },
        )

    @app.route("/api/jobs/<job_id>/report.pdf")
    def job_report_pdf(job_id):
        """The written report as a PDF (M3h §18b)."""
        result, job, error = owned_result(job_id)
        if error:
            return error
        if job["report_state"] != "ready":
            return _error("no_report", "There is no written report for this video.", status=409)
        if s3 is None or not bucket:
            return _not_configured()
        report = storage.get_report(s3, bucket, job["job_id"])
        if not report:
            return _error("no_report", "There is no written report for this video.", status=409)
        from neurolens import report_pdf

        data = report_pdf.report_pdf(job["filename"], result, report, frames_for(job["job_id"]))
        return (
            data,
            200,
            {
                "Content-Type": "application/pdf",
                "Content-Disposition": _disposition(
                    _stem(job["filename"]), "NeuroLens report.pdf", "NeuroLens report.pdf"
                ),
            },
        )

    @app.route("/api/jobs/<job_id>/brain")
    def job_brain(job_id):
        """Redirect to a five-minute presigned GET of the raw brain file (M3d §7)."""
        _, _, error = owned_result(job_id)
        if error:
            return error
        key = f"results/{uuid.UUID(job_id)}.brain"
        if not storage.object_exists(s3, bucket, key):
            return _error("brain_missing", "The brain file for this result is missing.", status=404)
        return redirect(
            storage.presigned_get(s3, bucket, key, 300, unique_id="neurolens-web-sigv4")
        )

    @app.route("/api/jobs/<job_id>/thumbnail")
    def job_thumbnail(job_id):
        """Redirect to a five-minute presigned GET of the video's thumbnail (M3d §7)."""
        job, error = owned_job(job_id)
        if error:
            return error
        if job["status"] != billing.DONE:
            return _error("thumbnail_missing", "No thumbnail for this job.", status=404)
        if s3 is None or not bucket:
            return _not_configured()
        key = f"results/{uuid.UUID(job_id)}.jpg"
        resp = redirect(
            storage.presigned_get(s3, bucket, key, 300, unique_id="neurolens-web-sigv4")
        )
        resp.headers["Cache-Control"] = "private, max-age=300"
        return resp

    @app.route("/api/jobs/<job_id>/video")
    def job_video(job_id):
        """A one-hour presigned GET of the caller's own video (M3d §7).

        The phone copy is tried first, then the upload, then a consented job's dataset copy.
        """
        _, job, error = owned_result(job_id)
        if error:
            return error
        key = job["object_key"]
        play_key = f"results/{job['job_id']}.play.mp4"
        if storage.object_exists(s3, bucket, play_key):
            key = play_key
        elif not storage.object_exists(s3, bucket, key):
            ext = PurePosixPath(key).suffix
            key = f"dataset/{job['job_id']}/video{ext}"
            if not (job["research_consent"] and storage.object_exists(s3, bucket, key)):
                return _error("video_deleted", "This video has been deleted.", status=404)
        return jsonify(
            {
                "url": storage.presigned_get(
                    s3, bucket, key, VIDEO_LINK_SECONDS, unique_id="neurolens-web-sigv4"
                ),
                "expires_in": VIDEO_LINK_SECONDS,
            }
        )

    @app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
    def cancel_job(job_id):
        """Refund and delete a queued job the caller owns, before the dispatcher prices it
        (M3d §7, M3e §8)."""
        job, error = owned_job(job_id)
        if error:
            return error
        if job["status"] != billing.QUEUED or job["verified_duration_ms"] is not None:
            return _error("not_cancellable", "This analysis has already started.", status=409)
        if s3 is None or not bucket:
            return _not_configured()
        if not billing.issue_refund(
            db, job["job_id"], "cancelled", queued=True, undispatched_only=True
        ):
            return _error("not_cancellable", "This analysis has already started.", status=409)
        try:
            deleted = storage.delete_keys(
                s3, bucket, storage.job_keys(job["job_id"], job["object_key"])
            )
        except Exception:
            logger.exception(f"Could not delete the files for cancelled {job['job_id']}")
        else:
            if not deleted:
                logger.error(f"S3 reported failed deletes for cancelled {job['job_id']}")
        return jsonify({"cancelled": True})

    @app.route("/api/jobs/<job_id>/stop-sharing", methods=["POST"])
    def stop_sharing(job_id):
        """Mark a shared job's research copy removed, then delete it (M3f §5c)."""
        job, error = owned_job(job_id)
        if error:
            return error
        if job["status"] in OPEN:
            return _job_active()
        if (
            job["status"] != billing.DONE
            or not job["research_consent"]
            or job["dataset_removed_at"] is not None
        ):
            return _error("not_shared", "This video is not shared.", status=409)
        if s3 is None or not bucket:
            return _not_configured()
        user_id, _ = signed_in_user()
        if not billing.mark_dataset_removed(db, job["job_id"], "stopped", user_id=user_id):
            return _error("not_shared", "This video is not shared.", status=409)
        try:
            billing.purge_job_files(db, s3, bucket, job["job_id"])
        except Exception:
            # The copy is marked removed, so no curve can use it; the reaper retries the files.
            logger.exception(f"Could not delete the research copy of {job['job_id']}")
        return jsonify({"stopped": True})

    @app.route("/api/jobs/<job_id>/retention/check", methods=["POST"])
    def job_retention_check(job_id):
        """What this curve would pay, before any work, without writing anything (M3j §6)."""
        job, error = shared_job(job_id)
        if error:
            return error
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error("invalid_request", "The request body must contain the video's link.")
        video_url = data.get("video_url")
        if not isinstance(video_url, str):
            return _error("invalid_request", "video_url must be the video's link.")
        try:
            platform, video_id = results.parse_video_link(video_url)
        except ValueError as err:
            return _error("invalid_video_link", str(err))

        facts, error = video_facts(platform, video_id, job["job_id"])
        if error:
            return error
        user_id, _ = signed_in_user()
        try:
            cents, reason = billing.curve_offer(
                db,
                user_id,
                job["job_id"],
                f"{platform}:{video_id}",
                facts,
                reward_cap,
                reward_window_days,
                length_tolerance_s,
                min_views=curve_min_views,
            )
        except billing.CurveRefused as err:
            return _error(err.code, err.message, status=err.status)
        return jsonify(
            {
                "platform": platform,
                "video_id": video_id,
                "reward_cents": cents,
                "reason": reason,
                "checked_views": facts.views if facts else None,
                "min_views": curve_min_views,
                "published_on": facts.published_at.date().isoformat() if facts else None,
                "video_seconds": facts.duration_s if facts else None,
                "analysed_seconds": round(job["verified_duration_ms"] / 1000)
                if job["verified_duration_ms"] is not None
                else None,
            }
        )

    @app.route("/api/jobs/<job_id>/retention")
    def job_retention_curve(job_id):
        """The newest quality-checked curve stored for the caller's own job (M3l §4)."""
        job, error = owned_job(job_id)
        if error:
            return error
        with db.transaction() as tx:
            rows = tx.execute(
                "SELECT csv_key, video_seconds FROM retention_snapshots "
                "WHERE job_id = CAST(:job_id AS uuid) "
                "AND (quality_flags IS NULL OR cardinality(quality_flags) = 0) "
                "ORDER BY added_at DESC, snapshot_id DESC LIMIT 1",
                {"job_id": job["job_id"]},
            )
        if not rows or not rows[0]["csv_key"]:
            return _not_found()
        if s3 is None or not bucket:
            return _not_configured()
        snapshot = rows[0]
        try:
            text = s3.get_object(Bucket=bucket, Key=snapshot["csv_key"])["Body"].read().decode()
        except Exception as err:
            if storage.is_not_found(err):
                return _not_found()
            raise
        duration_s = snapshot["video_seconds"]
        if duration_s is None and job["verified_duration_ms"] is not None:
            duration_s = job["verified_duration_ms"] / 1000
        if duration_s is None:
            return _not_found()
        points = results.retention_points(results.parse_retention_csv(text), duration_s)
        return jsonify({"points": points, "duration_s": duration_s})

    @app.route("/api/jobs/<job_id>/retention", methods=["POST"])
    def job_retention(job_id):
        """Store a shared job's audience-retention snapshot and reward it when valid (M3f §5d)."""
        job, error = shared_job(job_id)
        if error:
            return error

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error("invalid_request", "The request body must contain the report text.")
        video_url = data.get("video_url")
        if not isinstance(video_url, str):
            return _error("invalid_request", "video_url must be the video's link.")
        try:
            platform, video_id = results.parse_video_link(video_url)
        except ValueError as err:
            return _error("invalid_video_link", str(err))

        # The save does its own lookup: the server never trusts facts the page sends back.
        facts, error = video_facts(platform, video_id, job["job_id"])
        if error:
            return error
        text = data.get("report")
        if not isinstance(text, str):
            return _error("invalid_request", "The request body must contain the report text.")
        if len(text.encode()) > RETENTION_MAX_BYTES:
            return _error(
                "invalid_retention_report",
                f"The report must be at most {RETENTION_MAX_BYTES} bytes.",
                status=413,
            )
        try:
            if platform == "facebook":
                pairs = results.parse_facebook_retention(text, video_id)
            else:
                pairs = results.parse_retention_report(text)
        except ValueError as err:
            return _error("invalid_retention_report", str(err))

        if facts is not None:
            if data.get("views") is not None or data.get("published") is not None:
                return _error(
                    "invalid_request",
                    "We checked this video, so the views and published answers are not needed.",
                )
            views, published = None, None
        else:
            # Training weights only, never reward rules: optional, so a lookup that failed after
            # the check (which hid these questions) still saves.
            views = data.get("views")
            if views is not None and views not in RETENTION_VIEWS:
                return _error("invalid_request", "views must be one of the listed choices.")
            published = data.get("published")
            if published is not None and published not in RETENTION_PUBLISHED:
                return _error("invalid_request", "published must be one of the listed choices.")

        same_cut = data.get("same_cut")
        if same_cut not in {"same", "light_edit", "recut"}:
            return _error("invalid_request", "same_cut must be one of the listed choices.")
        promoted = data.get("promoted")
        if not isinstance(promoted, bool):
            return _error("invalid_request", "promoted must be true or false.")

        if s3 is None or not bucket:
            return _not_configured()
        csv_text = results.retention_csv(pairs)
        curve_sha256 = hashlib.sha256(csv_text.encode()).hexdigest()
        if billing.curve_exists(db, curve_sha256):
            return _error("duplicate_curve", "This curve was already added.", status=409)
        snapshot_id = str(uuid.uuid4())
        raw_key = f"dataset/{job['job_id']}/retention/{snapshot_id}.json"
        csv_key = f"dataset/{job['job_id']}/retention/{snapshot_id}.csv"
        written = []
        try:
            s3.put_object(
                Bucket=bucket,
                Key=raw_key,
                Body=text.encode(),
                ContentType="application/json",
            )
            written.append(raw_key)
            s3.put_object(
                Bucket=bucket,
                Key=csv_key,
                Body=csv_text.encode(),
                ContentType="text/csv",
            )
            written.append(csv_key)
        except Exception:
            logger.exception(f"Could not store the retention report for {job['job_id']}")
            if written:
                _delete_curve_files(s3, bucket, written, job["job_id"])
            return _error(
                "retention_store_failed",
                "Could not store the report. Please try again.",
                status=500,
            )
        user_id, _ = signed_in_user()
        try:
            stored = billing.add_curve(
                db,
                user_id,
                job["job_id"],
                snapshot_id=snapshot_id,
                raw_key=raw_key,
                csv_key=csv_key,
                curve_sha256=curve_sha256,
                points=pairs,
                quality_flags=results.curve_quality(pairs),
                views=views,
                published=published,
                promoted=promoted,
                reward_cap=reward_cap,
                reward_window_days=reward_window_days,
                video_ref=f"{platform}:{video_id}",
                same_cut=same_cut,
                source=f"{platform}_api",
                facts=facts,
                length_tolerance_s=length_tolerance_s,
                min_views=curve_min_views,
            )
        except (billing.DuplicateCurve, dbmod.DuplicateCurveViolation):
            _delete_curve_files(s3, bucket, [raw_key, csv_key], job["job_id"])
            return _error("duplicate_curve", "This curve was already added.", status=409)
        except billing.CurveRefused as err:
            _delete_curve_files(s3, bucket, [raw_key, csv_key], job["job_id"])
            return _error(err.code, err.message, status=err.status)
        return jsonify({"stored": True, **stored})

    @app.route("/api/jobs/<job_id>/report", methods=["POST"])
    def start_report(job_id):
        """Claim one free report attempt and start the report Lambda (M3g §4)."""
        job, error = owned_job(job_id)
        if error:
            return error
        if job["status"] != billing.DONE:
            return _not_found()
        if s3 is None or not bucket:
            return _not_configured()
        result = storage.get_result(s3, bucket, job["job_id"])
        if not result or not result.get("frames"):
            return _error("no_frames", "This video has no report.", status=409)
        state = job["report_state"]
        if state == "ready":
            return _error("report_ready", "The report is ready.", status=409)
        report_cfg = cfg.get("report") or {}
        stale_minutes = report_cfg["stale_minutes"]
        started = job["report_started_at"]
        stale = started is None or started < datetime.now(UTC) - timedelta(minutes=stale_minutes)
        if state == "writing" and not stale:
            return _error("report_writing", "The report is being written.", status=409)
        if job["report_tries"] >= report_cfg["max_tries"]:
            return _error("no_tries_left", "No tries left for this report.", status=429)
        if report_starter is None:
            return _not_configured()
        user_id, _ = signed_in_user()
        if not billing.claim_report(
            db, user_id, job["job_id"], report_cfg["max_tries"], stale_minutes
        ):
            return _error("report_writing", "The report is being written.", status=409)
        try:
            report_starter({"job_id": job["job_id"]})
        except Exception:
            logger.exception(f"Could not start the report for {job['job_id']}")
            billing.release_report_try(db, job["job_id"])
            return _error(
                "report_failed", "Could not start the report. Please try again.", status=503
            )
        return jsonify({"report_state": "writing"}), 202

    @app.route("/api/jobs/<job_id>/report")
    def get_report(job_id):
        """The report's state, tries left and stored answer (M3g §4)."""
        job, error = owned_job(job_id)
        if error:
            return error
        report_cfg = cfg.get("report") or {}
        state = job["report_state"]
        started = job["report_started_at"]
        stale_before = datetime.now(UTC) - timedelta(minutes=report_cfg["stale_minutes"])
        if state == "writing" and (started is None or started < stale_before):
            state = "failed"
        report = None
        if state == "ready" and s3 is not None and bucket:
            report = storage.get_report(s3, bucket, job["job_id"])
        return jsonify(
            {
                "report_state": state,
                "tries_left": max(0, report_cfg["max_tries"] - job["report_tries"]),
                "report": report,
            }
        )

    def comparison_ids(x, y):
        """(job_a, job_b, job_x, job_y, None) for two of the caller's jobs, or
        (None, None, None, None, an error). job_x is the video the caller opened."""
        job_x, error = owned_job(x)
        if error:
            return None, None, None, None, error
        job_y, error = owned_job(y)
        if error:
            return None, None, None, None, error
        try:
            return (*storage.comparison_pair(job_x["job_id"], job_y["job_id"]), job_x, job_y, None)
        except ValueError:
            return (
                None,
                None,
                None,
                None,
                _error("same_video", "Choose two different videos.", status=400),
            )

    @app.route("/api/compare/<x>/<y>/report", methods=["POST"])
    def start_comparison(x, y):
        """Claim one free comparison attempt and start the report Lambda (M3h §4)."""
        a, b, _, _, error = comparison_ids(x, y)
        if error:
            return error
        with db.transaction() as tx:
            rows = tx.execute(
                "SELECT job_id, status FROM jobs WHERE "
                "job_id IN (CAST(:a AS uuid), CAST(:b AS uuid))",
                {"a": a, "b": b},
            )
        if len(rows) != 2 or any(row["status"] != billing.DONE for row in rows):
            return _not_found()
        if s3 is None or not bucket:
            return _not_configured()
        result_a, result_b = (
            storage.get_result(s3, bucket, a),
            storage.get_result(s3, bucket, b),
        )
        if not result_a or not result_a.get("frames") or not result_b or not result_b.get("frames"):
            return _error("no_frames", "This pair has no report.", status=409)

        report_cfg = cfg.get("report") or {}
        max_tries = report_cfg["max_tries"]
        stale_minutes = report_cfg["stale_minutes"]
        row = billing.get_comparison(db, a, b)
        if row:
            state = row["state"]
            if state == "ready":
                return _error("report_ready", "The comparison is ready.", status=409)
            started = row["started_at"]
            stale = started is None or started < datetime.now(UTC) - timedelta(
                minutes=stale_minutes
            )
            if state == "writing" and not stale:
                return _error("report_writing", "The comparison is being written.", status=409)
            if row["tries"] >= max_tries:
                return _error("no_tries_left", "No tries left for this comparison.", status=429)
        if report_starter is None:
            return _not_configured()
        user_id, _ = signed_in_user()
        if not billing.claim_comparison(db, user_id, a, b, max_tries, stale_minutes):
            return _error("report_writing", "The comparison is being written.", status=409)
        try:
            report_starter({"pair": [a, b]})
        except Exception:
            logger.exception(f"Could not start the comparison for {a} and {b}")
            billing.release_comparison_try(db, a, b)
            return _error(
                "report_failed", "Could not start the comparison. Please try again.", status=503
            )
        return jsonify({"report_state": "writing"}), 202

    @app.route("/api/compare/<x>/<y>/report")
    def get_comparison(x, y):
        """The comparison's state, tries left and stored answer, turned so x is A (M3h §4, §2)."""
        a, b, job_x, job_y, error = comparison_ids(x, y)
        if error:
            return error
        report_cfg = cfg.get("report") or {}
        row = billing.get_comparison(db, a, b)
        state, tries, report = None, 0, None
        if row:
            state, tries = row["state"], row["tries"]
            started = row["started_at"]
            stale_before = datetime.now(UTC) - timedelta(minutes=report_cfg["stale_minutes"])
            if state == "writing" and (started is None or started < stale_before):
                state = "failed"
            if state == "ready" and s3 is not None and bucket:
                report = storage.get_comparison_report(s3, bucket, a, b)
        if report is not None:
            report = report_prompt.orient_comparison(report, flip=job_x["job_id"] != a)
        return jsonify(
            {
                "a": job_x["job_id"],
                "b": job_y["job_id"],
                "report_state": state,
                "tries_left": max(0, report_cfg["max_tries"] - tries),
                "report": report,
            }
        )

    @app.route("/api/compare/<x>/<y>/report.pdf")
    def comparison_report_pdf(x, y):
        """The written comparison as a PDF, turned so x is A and its name first (M3h §18b, §2)."""
        a, b, job_x, job_y, error = comparison_ids(x, y)
        if error:
            return error
        result_x, _, error = owned_result(job_x["job_id"])
        if error:
            return error
        result_y, _, error = owned_result(job_y["job_id"])
        if error:
            return error
        row = billing.get_comparison(db, a, b)
        if not row or row["state"] != "ready":
            return _error("no_report", "There is no written comparison for this pair.", status=409)
        if s3 is None or not bucket:
            return _not_configured()
        report = storage.get_comparison_report(s3, bucket, a, b)
        if not report:
            return _error("no_report", "There is no written comparison for this pair.", status=409)
        report = report_prompt.orient_comparison(report, flip=job_x["job_id"] != a)
        from neurolens import report_pdf

        data = report_pdf.comparison_pdf(
            (job_x["filename"], result_x, frames_for(job_x["job_id"])),
            (job_y["filename"], result_y, frames_for(job_y["job_id"])),
            report,
        )
        stem_x, stem_y = _stem(job_x["filename"]), _stem(job_y["filename"])
        stem = f"{stem_x} vs {stem_y}" if stem_x and stem_y else (stem_x or stem_y)
        return (
            data,
            200,
            {
                "Content-Type": "application/pdf",
                "Content-Disposition": _disposition(
                    stem, "NeuroLens comparison.pdf", "NeuroLens comparison.pdf"
                ),
            },
        )

    @app.route("/api/jobs/<job_id>", methods=["DELETE"])
    def delete_job(job_id):
        """Mark the caller's job deleted, then delete every file it owns (M3c §4, M3f §5c)."""
        job, error = owned_job(job_id)
        if error:
            return error
        if job["status"] in OPEN:
            return _job_active()
        if s3 is None or not bucket:
            return _not_configured()
        user_id, _ = signed_in_user()
        if not billing.delete_job(db, user_id, job["job_id"]):
            return _not_found()
        try:
            billing.purge_job_files(db, s3, bucket, job["job_id"])
        except Exception:
            # The job is already hidden, so the user cannot retry; the reaper finishes the purge.
            logger.exception(f"Could not delete the files of {job['job_id']}")
        return jsonify({"deleted": True})

    @app.route("/api/account/delete", methods=["POST"])
    def delete_account():
        """Mark the caller's account deleted, remove its logins, purge its files and rows
        (M3c §5)."""
        if not cognito:
            return _not_found()
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or data.get("confirm") != "DELETE":
            return _error("confirmation_required", "Type DELETE to confirm.", status=400)
        if db is None or s3 is None or not bucket:
            return _not_configured()
        user_id, _ = signed_in_user()
        try:
            identities = billing.delete_account(db, user_id)
        except billing.JobActive:
            return _job_active()

        failed = False
        for issuer, subject in identities:
            if issuer != auth_cfg["cognito_issuer"]:
                continue
            usernames = []
            try:
                response = cognito_idp.list_users(
                    UserPoolId=user_pool_id, Filter=f'sub = "{subject}"'
                )
                usernames = [user["Username"] for user in response.get("Users", [])]
                for username in usernames:
                    cognito_idp.admin_delete_user(UserPoolId=user_pool_id, Username=username)
            except Exception:
                username = usernames[0] if usernames else None
                logger.error(
                    f"COGNITO_DELETE_FAILED sub={subject} username={username}", exc_info=True
                )
                failed = True

        with db.transaction() as tx:
            jobs = tx.execute(
                "SELECT job_id FROM jobs WHERE user_id = :user_id", {"user_id": user_id}
            )
        for job in jobs:
            try:
                billing.purge_job_files(db, s3, bucket, job["job_id"])
            except Exception:
                # The account is marked, so the reaper retries; the user cannot act on it now.
                logger.exception(f"Could not delete a research copy of account {user_id}")
        try:
            billing.remove_deleted_account(db, user_id)
        except Exception:
            logger.exception(f"Could not remove the rows of account {user_id}; the reaper retries")
        session.clear()
        if failed:
            response, status = _error(
                "login_delete_failed",
                "Your data has been deleted. Removing your login failed; we have been notified.",
                status=500,
            )
            return auth.clear_signed_in_hint(response), status
        return auth.clear_signed_in_hint(jsonify({"deleted": True}))

    return app
