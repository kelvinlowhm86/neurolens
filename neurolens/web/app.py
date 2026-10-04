"""Flask routes. The web tier never runs the model: analysis happens in the worker."""

import json
import logging
import math
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from neurolens import pricing, settings, storage

logger = logging.getLogger("neurolens")


def _is_number(value):
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _error(code, message, status=400, **extra):
    return jsonify({"error": code, "message": message, **extra}), status


def create_app(data_dir=None, cfg=None):
    """Build the Flask app.

    Never reads config.json itself (the launcher app.py loads it and passes cfg), so tests
    need no config file. With cfg=None the max duration is 120 and there is no S3 client.
    """
    root = settings.get_root()
    if data_dir is None:
        if cfg is not None and "paths" in cfg:
            data_dir = settings.resolve_paths(cfg, root)["data"]
        else:
            data_dir = root / "data"

    data_dir = Path(data_dir)
    # The S3 client exists only when the settings have an aws block, which .env fills in (M0-style
    # tests have none).
    s3 = None
    if cfg is not None and "aws" in cfg:
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
        s3 = session.client("s3")
    max_bytes = cfg.get("max_upload_bytes") if cfg else None
    max_seconds = settings.max_duration(cfg)
    samples_json = data_dir / "samples.json"
    static_dir = root / "static"

    app = Flask(__name__, static_folder=str(static_dir))
    CORS(app)
    app.config["MAX_DURATION"] = max_seconds
    app.config["SAMPLES_JSON"] = samples_json

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

    @app.route("/api/uploads/presign", methods=["POST"])
    def presign():
        """Give the browser a one-off signed form to upload a video straight to S3."""
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
        # S3's size cap and the worker's head_object / ffprobe checks.
        duration = data.get("client_duration_seconds")
        if not _is_number(duration) or duration <= 0:
            return _error("invalid_request", "client_duration_seconds must be a positive number.")
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

        try:
            if s3 is None:
                raise RuntimeError("no AWS settings (NEUROLENS_* in .env)")
            if max_bytes is None:
                # a missing limit must never mean "unlimited"
                raise RuntimeError("config.json has no max_upload_bytes")
            presigned = storage.presign_upload(s3, cfg["aws"]["s3_bucket"], content_type, max_bytes)
        except Exception:
            logger.exception("Presign failed")
            return _error("presign_failed", "Could not prepare the upload.", status=500)

        presigned["estimated_cost_usd"] = pricing.estimate_cost_usd(duration)
        return jsonify(presigned)

    def job_data(job_id, read):
        """Run `read(s3, bucket, job_id)` for a UUID job id.

        Returns (value, None), or (None, an error response).

        The status and result endpoints answer anyone who knows a job's (unguessable) UUID;
        M3a adds ownership checks.
        """
        try:
            uuid.UUID(job_id)
        except ValueError:
            return None, _error("bad_job_id", "The job id is not valid.")
        try:
            if s3 is None:
                raise RuntimeError("no AWS settings (NEUROLENS_* in .env)")
            return read(s3, cfg["aws"]["s3_bucket"], job_id), None
        except Exception:
            logger.exception(f"Could not read job {job_id}")
            return None, _error("storage_error", "Could not read the job.", status=500)

    def _not_found():
        return _error("not_found", "No such job yet.", status=404)

    @app.route("/api/jobs/<job_id>/status")
    def job_status(job_id):
        """A result's existence decides `done` (a crash can leave the status object behind);
        otherwise the worker's status object. 404 means no worker has taken the job yet."""

        def read(s3, bucket, job_id):
            if storage.result_exists(s3, bucket, job_id):
                return {"job_id": job_id, "status": "done"}
            return storage.get_status(s3, bucket, job_id)

        status, error = job_data(job_id, read)
        if error:
            return error
        return jsonify(status) if status is not None else _not_found()

    @app.route("/api/jobs/<job_id>/result")
    def job_result(job_id):
        result, error = job_data(job_id, storage.get_result)
        if error:
            return error
        return jsonify(result) if result is not None else _not_found()

    return app
