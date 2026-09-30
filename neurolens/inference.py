"""Model + atlas loading and inference. Heavy libraries are imported inside functions only."""

import logging
import subprocess

import numpy as np

from neurolens import settings
from neurolens.engagement import build_roi_masks

logger = logging.getLogger("neurolens")

# Filled by load_model().
model = None
roi_masks = None


def load_model(cfg=None):
    """Load the TRIBE v2 model and the Destrieux atlas (once) and build the ROI masks."""
    global model, roi_masks

    root = settings.get_root()
    if cfg is None:
        cfg = settings.load_config(root)
    paths = settings.resolve_paths(cfg, root)
    settings.ensure_dirs(paths)
    # HF env vars must be set before anything imports huggingface_hub.
    settings.configure_env(cfg, paths)

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

    roi_masks = build_roi_masks(labels_full, label_names)
    logger.info("Atlas and ROI masks ready.")


def run_inference(video_path):
    """Run TRIBE v2 inference on a video file. Returns (T, 20484) numpy array."""
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
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")


def gpu_info():
    """GPU name and peak memory use, or None when there is no CUDA GPU."""
    import torch

    if not torch.cuda.is_available():
        return None
    return {
        "device": torch.cuda.get_device_name(0),
        "peak_vram_gb": round(torch.cuda.max_memory_allocated(0) / 1e9, 2),
    }
