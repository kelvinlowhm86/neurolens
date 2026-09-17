"""
NeuroLens API — Video Engagement Analysis via Predicted Brain Activity

Flask server that:
1. Loads TRIBE v2 model and Destrieux atlas at startup
2. Accepts video uploads via POST /api/analyse
3. Serves pre-computed sample results via GET /api/samples
4. Serves sample videos and thumbnails from data/
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import torch
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# ─── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("neurolens")

# ─── Load config ────────────────────────────────────────────────────────────

# Anchor every path to the file's own directory, NOT the CWD — otherwise
# `python /path/to/app.py` from elsewhere reads no config and scatters a
# ~20 GB download into whichever directory the process happened to start in.
PROJECT_DIR = Path(__file__).resolve().parent

CONFIG_PATH = PROJECT_DIR / "config.json"
assert CONFIG_PATH.exists(), (
    f"{CONFIG_PATH} not found. Run: cp config.sample.json config.json  "
    "(config.json is gitignored — it holds your HuggingFace token.)"
)

with open(CONFIG_PATH) as f:
    CFG = json.load(f)

MODELS_DIR = (PROJECT_DIR / CFG["paths"]["models"]).resolve()
DATA_DIR = (PROJECT_DIR / CFG["paths"]["data"]).resolve()
OUTPUT_DIR = (PROJECT_DIR / CFG["paths"]["output"]).resolve()

for d in [MODELS_DIR, DATA_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── Set HuggingFace env vars BEFORE any HF imports ────────────────────────
#
# huggingface_hub freezes its cache paths into module-level constants the
# moment it is imported (see huggingface_hub/constants.py). Anything imported
# above this block must therefore NOT pull in huggingface_hub or transformers,
# or the weights silently land in ~/.cache/huggingface instead of ./models.
assert "huggingface_hub" not in sys.modules, (
    "huggingface_hub was imported before the cache env vars were set — "
    "model weights would go to ~/.cache/huggingface. Move that import below."
)

HF_TOKEN = CFG["hf_token"]
os.environ["HF_TOKEN"] = HF_TOKEN

# HF_HOME is the root the other HF caches derive from; the rest are set
# explicitly so they survive anyone overriding HF_HOME downstream.
os.environ["HF_HOME"] = str(MODELS_DIR)
os.environ["HF_HUB_CACHE"] = str(MODELS_DIR / "hub")
os.environ["HUGGINGFACE_HUB_CACHE"] = str(MODELS_DIR / "hub")  # legacy alias
os.environ["HF_ASSETS_CACHE"] = str(MODELS_DIR / "assets")
os.environ["HF_DATASETS_CACHE"] = str(MODELS_DIR / "datasets")
# hf_xet is installed, so downloads stream through its dedup chunk cache.
# Left unset it defaults under HF_HOME, but it is large enough to be worth pinning.
os.environ["HF_XET_CACHE"] = str(MODELS_DIR / "xet")
# torch.hub / torchvision weights would otherwise go to ~/.cache/torch.
os.environ["TORCH_HOME"] = str(MODELS_DIR / "torch")

os.environ["NILEARN_DATA"] = str(DATA_DIR / "nilearn")
os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = str(CFG.get("hf_download_timeout", 300))
os.environ["HF_HUB_HTTP_TIMEOUT"] = str(CFG.get("hf_download_timeout", 300))

# ─── Load model and atlas ──────────────────────────────────────────────────

logger.info("Loading TRIBE v2 model...")
from tribev2.demo_utils import TribeModel

model = TribeModel.from_pretrained(
    CFG["model"]["repo_id"],
    cache_folder=str(MODELS_DIR),
)
logger.info("TRIBE v2 loaded.")

logger.info("Loading Destrieux atlas...")
from nilearn import datasets as nl_datasets

destrieux = nl_datasets.fetch_atlas_surf_destrieux()
labels_lh = np.array(destrieux["map_left"])
labels_rh = np.array(destrieux["map_right"])
label_names = destrieux["labels"]
labels_full = np.concatenate([labels_lh, labels_rh])

# ─── Build ROI masks ───────────────────────────────────────────────────────

ROI_LABEL_MAP = {
    "ffa_faces": ["G_oc-temp_lat-fusifor"],
    "eba_bodies": ["S_oc-temp_lat", "G_temporal_inf"],
    "ppa_scenes": ["G_oc-temp_med-Parahip"],
    "sts_social": ["S_temporal_sup"],
    "auditory": ["G_temp_sup-G_T_transv", "G_temp_sup-Plan_tempo"],
}

roi_masks = {}
for roi_name, target_labels in ROI_LABEL_MAP.items():
    mask = np.zeros(len(labels_full), dtype=bool)
    for target in target_labels:
        for i, name in enumerate(label_names):
            if target in name:
                mask |= (labels_full == i)
    roi_masks[roi_name] = mask
    logger.info(f"  ROI {roi_name}: {mask.sum()} vertices")

logger.info("Atlas and ROI masks ready.")

# ─── Helper functions ──────────────────────────────────────────────────────


def normalize_01(arr):
    mn, mx = arr.min(), arr.max()
    if mx - mn < 1e-8:
        return np.zeros_like(arr)
    return (arr - mn) / (mx - mn)


def run_inference(video_path):
    """Run TRIBE v2 inference on a video file. Returns (T, 20484) numpy array."""
    logger.info(f"Building events from {video_path}...")
    events = model.get_events_dataframe(video_path=str(video_path))
    logger.info(f"Running predict()...")
    preds, segments = model.predict(events=events)
    return np.asarray(preds)


def strip_audio(input_path, output_path):
    """Remove audio track from video using ffmpeg."""
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(input_path), "-an", "-c:v", "copy", str(output_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")


def extract_engagement(preds_full, preds_noaudio):
    """Extract per-ROI engagement timeseries from prediction matrices."""
    T = preds_full.shape[0]

    roi_timeseries = {}
    for roi_name, mask in roi_masks.items():
        roi_timeseries[roi_name] = np.abs(preds_full[:, mask]).mean(axis=1)

    roi_timeseries["engagement_overall"] = np.mean(
        [roi_timeseries[k] for k in ROI_LABEL_MAP.keys()], axis=0
    )

    roi_normed = {name: normalize_01(ts) for name, ts in roi_timeseries.items()}

    aud_mask = roi_masks["auditory"]
    aud_full = np.abs(preds_full[:, aud_mask]).mean(axis=1)
    aud_noaudio = np.abs(preds_noaudio[:, aud_mask]).mean(axis=1)
    min_len = min(len(aud_full), len(aud_noaudio))
    aud_full = aud_full[:min_len]
    aud_noaudio = aud_noaudio[:min_len]

    aud_full_normed = normalize_01(aud_full)
    aud_noaudio_normed = normalize_01(aud_noaudio)

    timesteps = []
    for t in range(T):
        step = {
            "t": t,
            "engagement_overall": round(float(roi_normed["engagement_overall"][t]), 4),
            "regions": {
                "ffa_faces": round(float(roi_normed["ffa_faces"][t]), 4),
                "eba_bodies": round(float(roi_normed["eba_bodies"][t]), 4),
                "ppa_scenes": round(float(roi_normed["ppa_scenes"][t]), 4),
                "sts_social": round(float(roi_normed["sts_social"][t]), 4),
                "auditory": round(float(roi_normed["auditory"][t]), 4),
            },
            "auditory_with_audio": round(float(aud_full_normed[t]), 4) if t < min_len else None,
            "auditory_without_audio": round(float(aud_noaudio_normed[t]), 4) if t < min_len else None,
        }
        timesteps.append(step)

    return {
        "duration_seconds": int(T),
        "timesteps": timesteps,
    }


# ─── Flask app ─────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder="static")
CORS(app)

MAX_DURATION = CFG.get("max_video_duration_seconds", 120)
SAMPLES_JSON = DATA_DIR / "samples.json"


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/samples")
def get_samples():
    """Serve pre-computed sample results from data/samples.json."""
    if SAMPLES_JSON.exists():
        with open(SAMPLES_JSON) as f:
            return jsonify(json.load(f))
    return jsonify({"samples": []})


@app.route("/data/<path:filename>")
def serve_data(filename):
    """Serve video files and thumbnails from the data directory."""
    return send_from_directory(str(DATA_DIR), filename)


@app.route("/api/analyse", methods=["POST"])
def analyse():
    if "video" not in request.files:
        return jsonify({"error": "No video file provided"}), 400

    video_file = request.files["video"]
    if not video_file.filename:
        return jsonify({"error": "Empty filename"}), 400

    suffix = Path(video_file.filename).suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, dir="/tmp", delete=False) as tmp:
        video_file.save(tmp)
        tmp.flush()
        os.fsync(tmp.fileno())
        video_path = Path(tmp.name)

    try:
        probe_result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)],
            capture_output=True, text=True,
        )
        duration = float(json.loads(probe_result.stdout)["format"]["duration"])
        if duration > MAX_DURATION:
            return jsonify({"error": f"Video too long ({duration:.0f}s). Max is {MAX_DURATION}s."}), 400

        logger.info(f"Analysing video: {video_file.filename} ({duration:.1f}s)")
        t0 = time.time()

        logger.info("Running inference — full video with audio...")
        preds_full = run_inference(video_path)
        logger.info(f"  Full inference done. Shape: {preds_full.shape}")

        noaudio_path = video_path.with_suffix(".noaudio" + suffix)
        logger.info("Stripping audio...")
        strip_audio(video_path, noaudio_path)

        logger.info("Running inference — video only, no audio...")
        preds_noaudio = run_inference(noaudio_path)
        logger.info(f"  Video-only inference done. Shape: {preds_noaudio.shape}")

        result = extract_engagement(preds_full, preds_noaudio)
        result["filename"] = video_file.filename
        result["processing_time_seconds"] = round(time.time() - t0, 1)

        if torch.cuda.is_available():
            result["gpu"] = {
                "device": torch.cuda.get_device_name(0),
                "peak_vram_gb": round(torch.cuda.max_memory_allocated(0) / 1e9, 2),
            }

        logger.info(f"Analysis complete in {result['processing_time_seconds']}s")
        return jsonify(result)

    except Exception as e:
        logger.exception("Analysis failed")
        return jsonify({"error": str(e)}), 500

    finally:
        video_path.unlink(missing_ok=True)
        noaudio_path = video_path.with_suffix(".noaudio" + suffix)
        noaudio_path.unlink(missing_ok=True)


# ─── Run ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info(f"Starting NeuroLens API on http://0.0.0.0:5003")
    logger.info(f"GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'}")
    logger.info(f"Max video duration: {MAX_DURATION}s")
    logger.info(f"Samples JSON: {SAMPLES_JSON} ({'found' if SAMPLES_JSON.exists() else 'not found'})")
    app.run(host="0.0.0.0", port=5003, debug=False)