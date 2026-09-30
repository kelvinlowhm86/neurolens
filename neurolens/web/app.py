"""Flask routes. Built by create_app() so tests can run without the model."""

import json
import logging
import os
import tempfile
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from neurolens import settings
from neurolens.engagement import extract_engagement

logger = logging.getLogger("neurolens")


def create_app(load_model=True, data_dir=None):
    """Build the Flask app.

    load_model=True loads config.json, the model and the atlas (needs a GPU machine).
    load_model=False needs none of that: /api/analyse answers 503.
    """
    root = settings.get_root()
    cfg = None
    inference = None

    if load_model:
        # TEMPORARY (removed in M1): local use still analyses videos in this process.
        # The web tier must never import inference otherwise.
        from neurolens import inference

        cfg = settings.load_config(root)
        paths = settings.resolve_paths(cfg, root)
        settings.ensure_dirs(paths)
        inference.load_model(cfg)
        if data_dir is None:
            data_dir = paths["data"]
    elif data_dir is None:
        data_dir = root / "data"

    data_dir = Path(data_dir)
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
