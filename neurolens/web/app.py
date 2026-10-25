"""Flask routes. The web tier never runs the model: analysis happens in the worker.

From M3a the database owns users, credit and job state (docs/M3a_spec.md §6): presign reserves
credit before handing out the upload form, and status and result answer only the job's owner,
checked in the database before any S3 request. From M3b (docs/M3b_spec.md §2-§4, §7b): Cognito
sign-in on AWS, every /api/* route needs a signed-in user, POSTs take JSON only, no CORS, job
history and CSV export, uploads refused while GPU work is paused.
"""

import hashlib
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
VIDEO_LINK_SECONDS = 3600  # a video is streamed in pieces; one hour outlives a paused viewer
HISTORY_LIMIT, HISTORY_MAX = 50, 100
RETENTION_MAX_BYTES = 64 * 1024
RETENTION_VIEWS = {"lt_1k", "1k_10k", "10k_100k", "gt_100k"}
RETENTION_PUBLISHED = {"week", "month", "older"}
DEFAULT_REWARD_CAP = 5
DEFAULT_REWARD_WINDOW_DAYS = 30
JOB_ACTIVE_MESSAGE = "This video is still waiting or being processed. Try again when it finishes."


def _is_number(value):
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _error(code, message, status=400, **extra):
    return jsonify({"error": code, "message": message, **extra}), status


def _csv_disposition(filename):
    """The CSV's attachment name, "<video name> – NeuroLens.csv" (M3f §4.7). HTTP headers are
    latin-1, so the full name goes in RFC 5987's `filename*` and `filename` carries a plain-ASCII
    fallback for old browsers."""
    stem = PurePosixPath(filename).stem.strip() if isinstance(filename, str) else ""
    if not stem:
        return (
            "attachment; filename=\"NeuroLens result.csv\"; filename*=UTF-8''NeuroLens%20result.csv"
        )
    ascii_stem = "".join(
        c if c.isascii() and c.isprintable() and c not in '"\\' else "_" for c in stem
    )
    full = quote(f"{stem} \u2013 NeuroLens.csv", safe="")
    return f"attachment; filename=\"{ascii_stem} - NeuroLens.csv\"; filename*=UTF-8''{full}"


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


def _endpoint_paused(autoscaling, endpoint):
    """True when the SageMaker endpoint's scalable target has a maximum of 0 (stop_work.sh), so
    no upload could ever run. A missing target counts as paused. Raises if it cannot be read."""
    targets = autoscaling.describe_scalable_targets(
        ServiceNamespace="sagemaker",
        ResourceIds=[f"endpoint/{endpoint}/variant/main"],
        ScalableDimension="sagemaker:variant:DesiredInstanceCount",
    )["ScalableTargets"]
    return not targets or targets[0].get("MaxCapacity", 0) == 0


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
    response_cfg = cfg.get("response")
    if cfg:  # a launcher's settings must carry a usable block: fail at start-up, not per request
        results.validate_response_config(response_cfg)
    cognito = auth_cfg["mode"] == "cognito"
    gpu_endpoint = (cfg.get("aws") or {}).get("gpu_endpoint")
    if cognito and not gpu_endpoint:
        raise ValueError("Missing required setting in Cognito mode: aws.gpu_endpoint")
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
    upload_cfg = cfg.get("upload") or {}
    max_batch_files = upload_cfg.get("max_batch_files")
    max_waiting_jobs = upload_cfg.get("max_waiting_jobs")
    research_cfg = cfg.get("research") or {}
    reward_cap = research_cfg.get("curve_rewards_per_window", DEFAULT_REWARD_CAP)
    reward_window_days = research_cfg.get("curve_reward_window_days", DEFAULT_REWARD_WINDOW_DAYS)
    static_dir = root / "static"
    autoscaling = None
    if gpu_endpoint:
        import boto3

        autoscaling = boto3.client("application-autoscaling", region_name=region)
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
        """Every /api/* route except GET /api/limits: a signed-in user, and JSON for POSTs
        (cross-site forms cannot send it without a CORS check this app never grants). The limits
        are public so signed-out visitors' sample brains can be coloured (M3d §6)."""
        if not request.path.startswith("/api/"):
            return None
        if request.path == "/api/limits" and request.method == "GET":
            return None
        user = auth.current_user()
        if user is None:
            return _not_signed_in()
        if request.method == "POST" and request.content_length and not request.is_json:
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
        auth.end_session(db)
        return auth.clear_signed_in_hint(
            jsonify({"logout_url": auth.logout_url(auth_cfg) if cognito else "/"})
        )

    @app.route("/api/limits")
    def get_limits():
        """The upload limits, so the page holds no copy of them (config.json is the one place)."""
        limits = {
            "max_video_duration_seconds": max_seconds,
            "max_upload_bytes": max_bytes,
            "colour_range": (response_cfg or {}).get("colour_range"),
        }
        if max_batch_files is not None:
            limits["max_batch_files"] = max_batch_files
        return jsonify(limits)

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
            }
        )

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
                paused = _endpoint_paused(autoscaling, gpu_endpoint)
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
        jobs = billing.list_jobs(
            db,
            user_id,
            int(limit),
            reward_cap=reward_cap,
            reward_window_days=reward_window_days,
        )
        rows = []
        for job in jobs:
            row = {key: value for key, value in job.items() if key != "research_consent"}
            row["job_id"] = str(job["job_id"])
            row["created_at"] = storage.utc_text(job["created_at"])
            row["result_available"] = job["status"] == billing.DONE and job["created_at"] > cutoff
            rows.append(row)
        return jsonify(rows)

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
                "claimed_at, error_code, error_message, research_consent, retention_added_at, "
                "dataset_removed_at, dataset_removed_reason, verified_duration_ms FROM jobs "
                "WHERE job_id = CAST(:job_id AS uuid) AND user_id = :user_id "
                "AND deleted_at IS NULL",
                {"job_id": job_id, "user_id": user_id},
            )
        if not rows:
            return None, _not_found()
        return rows[0], None

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

    @app.route("/api/jobs/<job_id>/status")
    def job_status(job_id):
        """The job as the database has it: the database alone decides its status."""
        job, error = owned_job(job_id)
        if error:
            return error
        # Consent gates the retention upload, which needs a done job, so it is reported only then.
        hidden = {
            "object_key",
            "claimed_at",
            "retention_added_at",
            "dataset_removed_at",
            "dataset_removed_reason",
            "verified_duration_ms",
        }
        public = {
            key: value
            for key, value in job.items()
            if key not in hidden and (key != "research_consent" or job["status"] == billing.DONE)
        }
        return jsonify({**public, "updated_at": storage.utc_text(job["updated_at"])})

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
                "Content-Disposition": _csv_disposition(job["filename"]),
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
        """A one-hour presigned GET of the caller's own video (M3d §7)."""
        _, job, error = owned_result(job_id)
        if error:
            return error
        key = job["object_key"]
        if not storage.object_exists(s3, bucket, key):
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

    @app.route("/api/jobs/<job_id>/retention", methods=["POST"])
    def job_retention(job_id):
        """Store a shared job's audience-retention snapshot and reward it when valid (M3f §5d)."""
        job, error = owned_job(job_id)
        if error:
            return error
        if job["status"] != billing.DONE:
            return _error("job_not_done", "The job is not done yet.", status=409)
        if not job["research_consent"]:
            return _error("no_consent", "This video was not shared for research.", status=409)
        if job["dataset_removed_at"] is not None:
            message = billing.CURVE_REMOVED_MESSAGES.get(
                job["dataset_removed_reason"], billing.CURVE_REMOVED_MESSAGES["no_curve"]
            )
            return _error("dataset_removed", message, status=409)

        data = request.get_json(silent=True)
        text = data.get("report") if isinstance(data, dict) else None
        if not isinstance(text, str):
            return _error("invalid_request", "The request body must contain the report text.")
        if len(text.encode()) > RETENTION_MAX_BYTES:
            return _error(
                "invalid_retention_report",
                f"The report must be at most {RETENTION_MAX_BYTES} bytes.",
                status=413,
            )
        try:
            pairs = results.parse_retention_report(text)
        except ValueError as err:
            return _error("invalid_retention_report", str(err))

        views = data.get("views")
        if views not in RETENTION_VIEWS:
            return _error("invalid_request", "views must be one of the listed choices.")
        published = data.get("published")
        if published not in RETENTION_PUBLISHED:
            return _error("invalid_request", "published must be one of the listed choices.")
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
            )
        except (billing.DuplicateCurve, dbmod.DuplicateCurveViolation):
            _delete_curve_files(s3, bucket, [raw_key, csv_key], job["job_id"])
            return _error("duplicate_curve", "This curve was already added.", status=409)
        except billing.CurveRefused as err:
            _delete_curve_files(s3, bucket, [raw_key, csv_key], job["job_id"])
            return _error(err.code, err.message, status=err.status)
        return jsonify({"stored": True, **stored})

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
