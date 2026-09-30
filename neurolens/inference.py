"""Model + atlas loading and inference. Heavy libraries are imported inside functions only."""

import json
import logging
import math
import os
import subprocess

import numpy as np

from neurolens import settings
from neurolens.engagement import build_roi_masks

logger = logging.getLogger("neurolens")

N_VERTICES = 20484

# Filled by load_model().
model = None
_roi_masks = None


def fake_mode():
    """True when FAKE_INFERENCE is 1/true/yes. Read from the environment at call time.

    An environment variable only (no config field), so a stale config file can never leave a
    real deployment in fake mode.
    """
    return os.environ.get("FAKE_INFERENCE", "").strip().lower() in ("1", "true", "yes")


def roi_masks():
    """The ROI masks built by load_model()."""
    return _roi_masks


def load_model(cfg=None):
    """Load the TRIBE v2 model (skipped in fake mode) and the Destrieux atlas, build ROI masks."""
    global model, _roi_masks

    root = settings.get_root()
    if cfg is None:
        cfg = settings.load_settings(root)
    paths = settings.resolve_paths(cfg, root)
    settings.ensure_dirs(paths)
    # HF env vars must be set before anything imports huggingface_hub.
    settings.configure_env(cfg, paths)

    if fake_mode():
        logger.warning("FAKE_INFERENCE is ON — results are not real model output")
    else:
        logger.info("Loading TRIBE v2 model...")
        from tribev2.demo_utils import TribeModel

        model = TribeModel.from_pretrained(
            cfg["model"]["repo_id"],
            cache_folder=str(paths["models"]),
        )
        logger.info("TRIBE v2 loaded.")

    logger.info("Loading Destrieux atlas...")
    from nilearn import datasets as nl_datasets

    destrieux = nl_datasets.fetch_atlas_surf_destrieux()
    labels_lh = np.array(destrieux["map_left"])
    labels_rh = np.array(destrieux["map_right"])
    label_names = destrieux["labels"]
    labels_full = np.concatenate([labels_lh, labels_rh])

    _roi_masks = build_roi_masks(labels_full, label_names)
    logger.info("Atlas and ROI masks ready.")


class UnreadableVideo(ValueError):
    """ffprobe could not produce a duration: not a video, corrupt, or no duration in the header."""


def probe_duration(path):
    """Video length in seconds, measured with ffprobe. Raises UnreadableVideo if unreadable."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as err:
        raise UnreadableVideo(f"ffprobe timed out on {path}") from err
    try:
        info = json.loads(result.stdout)
        duration = float(info["format"]["duration"])
        has_video = any(s.get("codec_type") == "video" for s in info.get("streams", []))
    except (ValueError, KeyError, TypeError, AttributeError) as err:  # JSONDecodeError too
        raise UnreadableVideo(f"ffprobe could not read a duration from {path}") from err
    if not has_video:
        # e.g. an audio-only .mp4: it would pass the length check, then fail at strip_audio
        raise UnreadableVideo(f"{path} has no video stream")
    if not math.isfinite(duration):
        raise UnreadableVideo(f"ffprobe gave a non-finite duration for {path}")
    return duration


def run_inference(video_path):
    """Run TRIBE v2 inference on a video file. Returns (T, 20484) numpy array.

    In fake mode: seeded random numbers of the right shape, so the same video gives the same
    output. Needs no model and no load_model() call.
    """
    if fake_mode():
        rows = math.ceil(probe_duration(video_path))
        rng = np.random.default_rng(os.path.getsize(video_path))
        return rng.standard_normal((rows, N_VERTICES))

    logger.info(f"Building events from {video_path}...")
    events = model.get_events_dataframe(video_path=str(video_path))
    logger.info("Running predict()...")
    preds, segments = model.predict(events=events)
    return np.asarray(preds)


def strip_audio(input_path, output_path):
    """Remove audio track from video using ffmpeg."""
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(input_path), "-an", "-c:v", "copy", str(output_path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")


def gpu_info():
    """GPU name and peak memory use, or None when there is no CUDA GPU."""
    try:
        import torch
    except ImportError:  # laptop / fake mode: torch is not installed
        return None

    if not torch.cuda.is_available():
        return None
    return {
        "device": torch.cuda.get_device_name(0),
        "peak_vram_gb": round(torch.cuda.max_memory_allocated(0) / 1e9, 2),
    }
