"""
Real TRIBE v2 backend.

Two caches, kept apart on purpose:
  * weights  → HF_HOME (/models, read-only on AWS, baked into the AMI)
  * features → `cache_folder` passed to TribeModel (encoder outputs, keyed per input file path).
    This must be writable, so it lives under SCRATCH_DIR. It is wiped once at worker start, never
    between jobs: the cache library indexes its folders in memory, and deleting them mid-life breaks
    the next lookup. Entries are small (~10–30 MB per video) and each job's input path is unique.

Silent pass (SILENT_MODE):
  * "ffmpeg" — strip the audio track and run the whole pipeline again (report-literal, safe default).
  * "events" — reuse the full pass's events with audio/speech rows dropped. The video file path is
    unchanged, so the cached V-JEPA2 video features are reused and only the transformer reruns.
    Must pass the Part 5 equivalence check before it becomes the default.
"""

import logging
import shutil
from pathlib import Path

import numpy as np

from neurolens_common.config import Settings

from .. import media
from ..roi import load_destrieux_masks

logger = logging.getLogger("neurolens.worker.tribe")


class TribeBackend:
    name = "tribe"

    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.feature_cache = cfg.scratch_dir / "feature-cache"
        self.model = None
        self.model_revision: str | None = None
        self.roi_masks: dict[str, np.ndarray] = {}
        self._full_events = None   # events of the current job's full pass (for SILENT_MODE=events)

    def load(self) -> None:
        import torch  # noqa: F401  (import before tribev2 so CUDA init errors surface clearly)
        from tribev2.demo_utils import TribeModel

        shutil.rmtree(self.feature_cache, ignore_errors=True)
        self.feature_cache.mkdir(parents=True, exist_ok=True)
        self.model = TribeModel.from_pretrained(self.cfg.tribe_repo, cache_folder=str(self.feature_cache))
        self.model_revision = _hf_revision(self.cfg.tribe_repo)
        self.roi_masks = load_destrieux_masks()
        logger.info("TRIBE v2 loaded (revision %s); ROI vertices: %s", self.model_revision,
                    {k: int(v.sum()) for k, v in self.roi_masks.items()})

    def warmup(self, clip: Path | None = None) -> None:
        """
        One short prediction so the lazily-loaded encoders are on the GPU before the first job.
        With `clip` containing speech, this also exercises WhisperX + LLaMA (used by the AMI bake).
        """
        if clip is None:
            clip = media.make_test_clip(self.cfg.scratch_dir / "warmup.mp4", seconds=4)
        self.predict_full(clip, 0)
        self.end_job()

    def _predict_events(self, events) -> np.ndarray:
        preds, _segments = self.model.predict(events=events, verbose=False)
        return np.asarray(preds)

    def predict_full(self, path: Path, duration_s: float) -> np.ndarray:
        events = self.model.get_events_dataframe(video_path=str(path))
        self._full_events = events
        return self._predict_events(events)

    def predict_silent(self, path: Path, duration_s: float) -> np.ndarray:
        if self.cfg.silent_mode == "events" and self._full_events is not None:
            events = self._full_events[self._full_events["type"] == "Video"].reset_index(drop=True)
            return self._predict_events(events)
        silent = path.with_name(path.stem + ".noaudio" + path.suffix)
        media.strip_audio(path, silent)
        try:
            return self._predict_events(self.model.get_events_dataframe(video_path=str(silent)))
        finally:
            silent.unlink(missing_ok=True)

    def transcript(self) -> list[dict]:
        ev = self._full_events
        if ev is None or "Word" not in set(ev["type"]):
            return []
        words = ev[ev["type"] == "Word"]
        return [{"start": float(r.start), "text": str(r.text)} for r in words.itertuples() if str(r.text).strip()]

    def end_job(self) -> None:
        import torch

        self._full_events = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()

    def gpu_device(self) -> str | None:
        import torch

        return torch.cuda.get_device_name(0) if torch.cuda.is_available() else None

    def peak_vram_gb(self) -> float | None:
        import torch

        if not torch.cuda.is_available():
            return None
        return round(max(torch.cuda.max_memory_allocated(i) for i in range(torch.cuda.device_count())) / 1e9, 2)


def _hf_revision(repo_id: str) -> str | None:
    """Commit hash of the cached checkpoint snapshot (recorded in every result for reproducibility)."""
    try:
        from huggingface_hub import scan_cache_dir

        for repo in scan_cache_dir().repos:
            if repo.repo_id == repo_id:
                return sorted(repo.revisions, key=lambda r: r.last_modified)[-1].commit_hash
    except Exception:
        logger.warning("Could not read checkpoint revision", exc_info=True)
    return None
