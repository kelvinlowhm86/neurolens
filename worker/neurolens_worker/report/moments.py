"""
Pick the moments worth a screenshot, straight from the TRIBE result: the hook, the overall peak and
trough, the sharpest drop, the biggest audio lift, each region's strongest second, and the ending.
Moments closer than 2 s to an earlier pick are skipped so the frames show different shots.
"""

from dataclasses import dataclass

import numpy as np

from neurolens_common.schemas import REGIONS

REGION_LABEL = {"ffa_faces": "faces peak (FFA)", "eba_bodies": "bodies peak (EBA)",
                "ppa_scenes": "scenes peak (PPA)", "sts_social": "social peak (STS)", "auditory": "audio peak"}


@dataclass(frozen=True)
class Moment:
    frame_id: int
    t: int            # second index; the frame is grabbed at t + 0.5 s
    reason: str


def pick_moments(result: dict, max_frames: int = 8, min_gap: int = 2) -> list[Moment]:
    ts = result["timesteps"]
    n = len(ts)
    if n == 0:
        return []
    eng = np.array([t["engagement_overall"] for t in ts])
    candidates: list[tuple[int, str]] = [(min(1, n - 1), "opening hook")]
    candidates.append((int(np.argmax(eng)), "overall peak"))
    if n > 4:
        candidates.append((int(np.argmin(eng[2:]) + 2), "overall low point"))
    if n > 3:
        drops = eng[2:] - eng[:-2]                        # change over 2 s
        candidates.append((int(np.argmin(drops) + 2), "sharpest drop"))
    with_a = [t["auditory_with_audio"] for t in ts]
    without_a = [t["auditory_without_audio"] for t in ts]
    lift = [(a - b) if a is not None and b is not None else -1 for a, b in zip(with_a, without_a)]
    if max(lift) > 0:
        candidates.append((int(np.argmax(lift)), "biggest audio lift"))
    for region in REGIONS:
        series = np.array([t["regions"][region] for t in ts])
        candidates.append((int(np.argmax(series)), REGION_LABEL[region]))
    candidates.append((n - 1, "ending"))

    picked: list[tuple[int, str]] = []
    for t, reason in candidates:
        if len(picked) >= max_frames:
            break
        if all(abs(t - p) >= min_gap for p, _ in picked):
            picked.append((t, reason))
    picked.sort()
    return [Moment(frame_id=i + 1, t=t, reason=reason) for i, (t, reason) in enumerate(picked)]
