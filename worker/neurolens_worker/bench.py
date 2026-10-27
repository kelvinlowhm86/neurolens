"""
Offline measurements on a GPU box (no queue, no DB):

  bench        per-video timings for Experiment 3 (cloud vs on-prem): one CSV row per video
  silent-check compare SILENT_MODE=events against =ffmpeg on one video — same answer? how much faster?
"""

import csv
import logging
import shutil
import time
from pathlib import Path

import numpy as np

from neurolens_common.config import Settings

from . import media

logger = logging.getLogger("neurolens.worker.bench")

FIELDS = ["video", "duration_s", "pass_full_s", "pass_silent_s", "total_s", "peak_vram_gb", "gpu",
          "silent_mode", "host"]


def bench(cfg: Settings, videos: list[Path], out_csv: Path, host: str) -> None:
    from .inference.tribe import TribeBackend

    backend = TribeBackend(cfg)
    backend.load()
    backend.warmup()
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    new_file = not out_csv.exists()
    with out_csv.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        for src in videos:
            # Work on a fresh copy, like a real job: tribev2 caches .wav/.tsv (transcript) files next to
            # its input, and reusing old ones would skip WhisperX and understate the time.
            job_dir = cfg.scratch_dir / "bench" / src.stem
            shutil.rmtree(job_dir, ignore_errors=True)
            job_dir.mkdir(parents=True)
            video = job_dir / f"input{src.suffix}"
            shutil.copy(src, video)
            p = media.probe(video)
            t0 = time.monotonic()
            backend.predict_full(video, p.duration_s)
            t_full = time.monotonic() - t0
            t0 = time.monotonic()
            backend.predict_silent(video, p.duration_s)
            t_silent = time.monotonic() - t0
            row = {"video": src.name, "duration_s": round(p.duration_s, 2), "pass_full_s": round(t_full, 2),
                   "pass_silent_s": round(t_silent, 2), "total_s": round(t_full + t_silent, 2),
                   "peak_vram_gb": backend.peak_vram_gb(), "gpu": backend.gpu_device(),
                   "silent_mode": cfg.silent_mode, "host": host}
            backend.end_job()
            shutil.rmtree(job_dir, ignore_errors=True)
            w.writerow(row)
            f.flush()
            logger.info("%s", row)


def silent_check(cfg: Settings, video: Path) -> dict:
    """Run the full pass once, then the silent pass both ways, and compare."""
    from .inference.tribe import TribeBackend

    backend = TribeBackend(cfg.model_copy(update={"silent_mode": "events"}))
    backend.load()
    backend.warmup()
    p = media.probe(video)
    aud = backend.roi_masks["auditory"]

    backend.predict_full(video, p.duration_s)
    t0 = time.monotonic()
    via_events = backend.predict_silent(video, p.duration_s)
    t_events = time.monotonic() - t0

    backend.cfg = cfg.model_copy(update={"silent_mode": "ffmpeg"})
    t0 = time.monotonic()
    via_ffmpeg = backend.predict_silent(video, p.duration_s)
    t_ffmpeg = time.monotonic() - t0
    backend.end_job()

    n = min(len(via_events), len(via_ffmpeg))
    a = np.abs(via_events[:n, aud]).mean(axis=1)
    b = np.abs(via_ffmpeg[:n, aud]).mean(axis=1)
    report = {
        "video": video.name,
        "timesteps_events": len(via_events),
        "timesteps_ffmpeg": len(via_ffmpeg),
        "auditory_corr": round(float(np.corrcoef(a, b)[0, 1]), 4),
        "whole_brain_corr": round(float(np.corrcoef(via_events[:n].ravel(), via_ffmpeg[:n].ravel())[0, 1]), 4),
        "silent_events_s": round(t_events, 1),
        "silent_ffmpeg_s": round(t_ffmpeg, 1),
        "pass": None,
    }
    report["pass"] = report["auditory_corr"] > 0.99 and report["timesteps_events"] == report["timesteps_ffmpeg"]
    return report
