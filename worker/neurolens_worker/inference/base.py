"""The interface every inference backend implements (real TRIBE v2, or the mock used in tests)."""

from pathlib import Path
from typing import Protocol

import numpy as np


class Backend(Protocol):
    name: str
    model_revision: str | None
    roi_masks: dict[str, np.ndarray]

    def load(self) -> None:
        """Load weights + atlas. Called once at worker start."""

    def warmup(self, clip: Path | None = None) -> None:
        """Run one tiny prediction so lazily-loaded encoders are on the GPU before real jobs."""

    def predict_full(self, path: Path, duration_s: float) -> np.ndarray:
        """(T, 20484) predicted cortical response with video + audio (+ transcribed speech)."""

    def predict_silent(self, path: Path, duration_s: float) -> np.ndarray:
        """(T, 20484) prediction for the same video with the soundtrack removed."""

    def transcript(self) -> list[dict]:
        """Words heard in the last full pass: [{"start": seconds, "text": word}, ...] (empty if no speech)."""

    def end_job(self) -> None:
        """Free per-job state (feature cache, GPU memory)."""

    def gpu_device(self) -> str | None: ...

    def peak_vram_gb(self) -> float | None: ...
