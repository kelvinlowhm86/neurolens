"""
Deterministic fake backend: smooth random (T, 20484) arrays and synthetic ROI masks.

Lets the whole system (queue, billing, scaling, frontend) run and be tested without a GPU.
`MOCK_SECONDS_PER_PASS` makes each pass take a realistic-feeling amount of time.
"""

import math
import time
import zlib
from pathlib import Path

import numpy as np

from neurolens_common.schemas import REGIONS

from ..roi import N_VERTICES


class MockBackend:
    name = "mock"
    model_revision = None

    def __init__(self, seconds_per_pass: float = 2.0):
        self.seconds_per_pass = seconds_per_pass
        self.roi_masks: dict[str, np.ndarray] = {}

    def load(self) -> None:
        rng = np.random.default_rng(5224)
        order = rng.permutation(N_VERTICES)
        for i, roi in enumerate(REGIONS):                      # disjoint ~400-vertex ROIs
            mask = np.zeros(N_VERTICES, dtype=bool)
            mask[order[i * 400:(i + 1) * 400]] = True
            self.roi_masks[roi] = mask

    def warmup(self, clip: Path | None = None) -> None:
        time.sleep(min(self.seconds_per_pass, 1.0))

    def _predict(self, path: Path, duration_s: float, audio: bool) -> np.ndarray:
        time.sleep(self.seconds_per_pass)
        T = max(1, math.ceil(duration_s))
        rng = np.random.default_rng(zlib.crc32(path.name.encode()))
        t = np.arange(T)[:, None]
        phase = rng.uniform(0, 2 * np.pi, N_VERTICES)[None, :]
        preds = 0.1 * np.sin(t / 3 + phase) + 0.02 * rng.standard_normal((T, N_VERTICES))
        if not audio:
            preds[:, self.roi_masks["auditory"]] *= 0.5        # the soundtrack drives auditory cortex
        return preds.astype(np.float32)

    def predict_full(self, path: Path, duration_s: float) -> np.ndarray:
        return self._predict(path, duration_s, audio=True)

    def predict_silent(self, path: Path, duration_s: float) -> np.ndarray:
        return self._predict(path, duration_s, audio=False)

    def transcript(self) -> list[dict]:
        return []

    def end_job(self) -> None:
        pass

    def gpu_device(self) -> str | None:
        return None

    def peak_vram_gb(self) -> float | None:
        return None
