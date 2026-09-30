"""Flask routes. Built by create_app() so tests can run without the model."""

import json
import logging
import os
import tempfile
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from neurolens import pricing, settings, storage
from neurolens.engagement import extract_engagement

logger = logging.getLogger("neurolens")


def _is_number(value):
    return isinstance(value, int | float) and not isinstance(value, bool)


def _error(code, message, status=400, **extra):
    return jsonify({"error": code, "message": message, **extra}), status


def create_app(load_model=True, data_dir=None, cfg=None):
    """Build the Flask app.

    load_model=True loads config.json, the model and the atlas (needs a GPU machine).
    load_model=False needs none of that: /api/analyse answers 503.
    cfg, when given, is used instead of reading config.json (tests need no config file).
    """
    root = settings.get_root()
    inference = None

    if load_model:
        # TEMPORARY (removed at the end of M1): local use still analyses videos in this
        # process. The web tier must never import inference otherwise.
        from neurolens import inference

        if cfg is None:
            cfg = settings.load_config(root)
        paths = settings.resolve_paths(cfg, root)
        settings.ensure_dirs(paths)
        inference.load_model(cfg)
        if data_dir is None:
            data_dir = paths["data"]
    elif data_dir is None:
        data_dir = root / "data"

    data_dir = Path(data_dir)
    # The S3 client exists only when the config has an aws block (M0-style tests have none).
    s3 = None
    if cfg is not None and "aws" in cfg:
        import boto3

        s3 = boto3.client("s3", region_name=cfg["aws"]["region"])
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

    @app.route("/data/<path:filename>")
    def serve_data(filename):
        """Serve video files and thumbnails from the data directory."""
        return send_from_directory(str(data_dir), filename)

    @app.route("/api/uploads/presign", methods=["POST"])
    def presign():
        """Give the browser a one-off signed form to upload a video straight to S3."""
        data = request.get_json(silent=True) or {}

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
                raise RuntimeError("config.json has no aws block")
            presigned = storage.presign_upload(s3, cfg["aws"]["s3_bucket"], content_type, max_bytes)
        except Exception:
            logger.exception("Presign failed")
            return _error("presign_failed", "Could not prepare the upload.", status=500)

        presigned["estimated_cost_usd"] = pricing.estimate_cost_usd(duration)
        return jsonify(presigned)

    # TODO(M1-cleanup): remove this endpoint once the presign+SQS+worker path
    # is verified end-to-end (see M1 spec §9 acceptance criteria). Do not
    # maintain both paths past this milestone.
    @app.route("/api/analyse", methods=["POST"])
    def analyse():
        if "video" not in request.files:
            return jsonify({"error": "No video file provided"}), 400

        video_file = request.files["video"]
        if not video_file.filename:
            return jsonify({"error": "Empty filename"}), 400

        if inference is None:
            return jsonify({"error": "Model not loaded"}), 503

        suffix = Path(video_file.filename).suffix or ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix, dir="/tmp", delete=False) as tmp:
            video_file.save(tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
            video_path = Path(tmp.name)

        try:
            duration = inference.probe_duration(video_path)
            if duration > max_seconds:
                return (
                    jsonify({"error": f"Video too long ({duration:.0f}s). Max is {max_seconds}s."}),
                    400,
                )

            logger.info(f"Analysing video: {video_file.filename} ({duration:.1f}s)")
            t0 = time.time()

            logger.info("Running inference — full video with audio...")
            preds_full = inference.run_inference(video_path)
            logger.info(f"  Full inference done. Shape: {preds_full.shape}")

            noaudio_path = video_path.with_suffix(".noaudio" + suffix)
            logger.info("Stripping audio...")
            inference.strip_audio(video_path, noaudio_path)

            logger.info("Running inference — video only, no audio...")
            preds_noaudio = inference.run_inference(noaudio_path)
            logger.info(f"  Video-only inference done. Shape: {preds_noaudio.shape}")

            result = extract_engagement(preds_full, preds_noaudio, inference.roi_masks())
            result["filename"] = video_file.filename
            result["processing_time_seconds"] = round(time.time() - t0, 1)

            gpu = inference.gpu_info()
            if gpu is not None:
                result["gpu"] = gpu

            logger.info(f"Analysis complete in {result['processing_time_seconds']}s")
            return jsonify(result)

        except Exception as e:
            logger.exception("Analysis failed")
            return jsonify({"error": str(e)}), 500

        finally:
            video_path.unlink(missing_ok=True)
            noaudio_path = video_path.with_suffix(".noaudio" + suffix)
            noaudio_path.unlink(missing_ok=True)

    return app
