"""Flask routes. The web tier never runs the model: analysis happens in the worker.

From M3a the database owns users, credit and job state (docs/M3a_spec.md §6): presign reserves
credit before handing out the upload form, and status and result answer only the job's owner,
checked in the database before any S3 request.
"""

import json
import logging
import math
import threading
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

from neurolens import billing, pricing, settings, storage
from neurolens import db as dbmod
from neurolens.web import auth

logger = logging.getLogger("neurolens")

DEFAULT_STARTER_CENTS = 500  # M3a §3b
MIN_DURATION_SECONDS = 1  # a zero-length job would cost nothing yet start a GPU
MAX_FILENAME_CHARS = 255  # the filename is display only
OPEN = (billing.QUEUED, billing.PROCESSING)


def _is_number(value):
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _error(code, message, status=400, **extra):
    return jsonify({"error": code, "message": message, **extra}), status


def _not_configured():
    return _error(
        "not_configured", "The server is missing its database or storage settings.", status=500
    )


def _not_found():
    return _error("not_found", "No such job, or no result yet.", status=404)


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


def create_app(*, cfg=None, data_dir=None, db=None, s3_client=None):
    """Build the Flask app.

    Never reads config.json itself (the launcher app.py loads it and passes cfg), so tests
    need no config file. Without cfg: the built-in development user, max duration 120, and no
    S3 client or database unless handed in. A route that needs a database or S3 client the app
    doesn't have answers 500 `not_configured`.
    """
    auth_cfg = auth.auth_settings(cfg)  # first: refuses the dev identity on a public address
    root = settings.get_root()
    if data_dir is None:
        if cfg is not None and "paths" in cfg:
            data_dir = settings.resolve_paths(cfg, root)["data"]
        else:
            data_dir = root / "data"
    data_dir = Path(data_dir)

    s3 = s3_client if s3_client is not None else _s3_client(cfg)
    if db is None and cfg is not None and cfg.get("db"):
        db = dbmod.from_config(cfg)
    bucket = ((cfg or {}).get("aws") or {}).get("s3_bucket")
    starter_cents = ((cfg or {}).get("billing") or {}).get("starter_cents", DEFAULT_STARTER_CENTS)
    max_bytes = cfg.get("max_upload_bytes") if cfg else None
    max_seconds = settings.max_duration(cfg)
    samples_json = data_dir / "samples.json"
    static_dir = root / "static"

    # Users this process has already made sure of, so a page costs no database write each time.
    known_users = set()
    known_lock = threading.Lock()

    app = Flask(__name__, static_folder=str(static_dir))
    CORS(app)
    app.config["MAX_DURATION"] = max_seconds
    app.config["SAMPLES_JSON"] = samples_json
    app.config["NEUROLENS_AUTH"] = auth_cfg

    @app.errorhandler(Exception)
    def unexpected(err):
        if isinstance(err, HTTPException):
            return err
        logger.exception("Unexpected error")
        return _error("server_error", "Something went wrong. Please try again.", status=500)

    def signed_in_user():
        """(user_id, email) of the caller, created with the starter credit on first sight."""
        user_id, email = auth.current_user()
        with known_lock:
            known = user_id in known_users
        if not known:
            billing.ensure_user(db, user_id, email, starter_cents)
            with known_lock:
                known_users.add(user_id)
        return user_id, email

    @app.route("/")
    def index():
        return send_from_directory(str(static_dir), "index.html")

    @app.route("/api/samples")
    def get_samples():
        """Serve pre-computed sample results from data/samples.json."""
        if samples_json.exists():
            with open(samples_json) as f:
                return jsonify(json.load(f))
        return jsonify({"samples": []})

    @app.route("/api/limits")
    def get_limits():
        """The upload limits, so the page holds no copy of them (config.json is the one place)."""
        return jsonify({"max_video_duration_seconds": max_seconds, "max_upload_bytes": max_bytes})

    @app.route("/data/<path:filename>")
    def serve_data(filename):
        """Serve video files and thumbnails from the data directory."""
        return send_from_directory(str(data_dir), filename)

    @app.route("/api/me")
    def me():
        if db is None:
            return _not_configured()
        user_id, email = signed_in_user()
        return jsonify({"email": email, **billing.get_balance(db, user_id)})

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
        """Only for a done job, or an unfinished one whose result exists (the crash window). A
        refunded job never serves a result, even one a late worker wrote."""
        job, error = owned_job(job_id)
        if error:
            return error
        if job["status"] == billing.FAILED:
            return _not_found()
        if s3 is None or not bucket:
            return _not_configured()
        result = storage.get_result(s3, bucket, job["job_id"])
        return jsonify(result) if result is not None else _not_found()

    return app
