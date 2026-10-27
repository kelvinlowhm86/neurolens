"""
Cortical ROI extraction — ported from the prototype `app.py` (`extract_engagement`).

The normalised fields are numerically identical to the prototype (guarded by
tests/test_roi_parity.py); `raw` and `summary` are new in result schema v2.
"""

import numpy as np

from neurolens_common.schemas import REGIONS

# Destrieux label substrings per functional region (see README "Regional Content Comparison").
ROI_LABEL_MAP = {
    "ffa_faces": ["G_oc-temp_lat-fusifor"],
    "eba_bodies": ["S_oc-temp_lat", "G_temporal_inf"],
    "ppa_scenes": ["G_oc-temp_med-Parahip"],
    "sts_social": ["S_temporal_sup"],
    "auditory": ["G_temp_sup-G_T_transv", "G_temp_sup-Plan_tempo"],
}
N_VERTICES = 20484  # fsaverage5, both hemispheres


def build_masks(labels_full: np.ndarray, label_names: list) -> dict[str, np.ndarray]:
    """Boolean vertex mask per ROI from a full-brain Destrieux label array."""
    masks = {}
    for roi_name, targets in ROI_LABEL_MAP.items():
        mask = np.zeros(len(labels_full), dtype=bool)
        for target in targets:
            for i, name in enumerate(label_names):
                if target in str(name):
                    mask |= labels_full == i
        masks[roi_name] = mask
    return masks


def load_destrieux_masks() -> dict[str, np.ndarray]:
    """Masks from nilearn's Destrieux atlas on fsaverage5 (reads NILEARN_DATA; baked into the AMI)."""
    from nilearn import datasets as nl_datasets

    destrieux = nl_datasets.fetch_atlas_surf_destrieux()
    labels_full = np.concatenate([np.array(destrieux["map_left"]), np.array(destrieux["map_right"])])
    return build_masks(labels_full, destrieux["labels"])


def normalize_01(arr: np.ndarray) -> np.ndarray:
    mn, mx = arr.min(), arr.max()
    if mx - mn < 1e-8:
        return np.zeros_like(arr)
    return (arr - mn) / (mx - mn)


def _r(x: float) -> float:
    return round(float(x), 4)


def extract(preds_full: np.ndarray, preds_silent: np.ndarray, masks: dict[str, np.ndarray]) -> dict:
    """
    (T, 20484) predictions with and without audio → per-second ROI timeseries.

    Returns {"duration_seconds", "timesteps", "raw", "summary"} (the rest of ResultV2 is
    filled in by the pipeline).
    """
    T = preds_full.shape[0]

    raw = {roi: np.abs(preds_full[:, masks[roi]]).mean(axis=1) for roi in REGIONS}
    raw["engagement_overall"] = np.mean([raw[k] for k in REGIONS], axis=0)
    normed = {name: normalize_01(ts) for name, ts in raw.items()}

    # Audio contribution: the same auditory ROI with the soundtrack removed.
    aud_full = raw["auditory"]
    aud_silent = np.abs(preds_silent[:, masks["auditory"]]).mean(axis=1)
    min_len = min(len(aud_full), len(aud_silent))
    aud_full_n = normalize_01(aud_full[:min_len])
    aud_silent_n = normalize_01(aud_silent[:min_len])

    timesteps = []
    for t in range(T):
        timesteps.append({
            "t": t,
            "engagement_overall": _r(normed["engagement_overall"][t]),
            "regions": {roi: _r(normed[roi][t]) for roi in REGIONS},
            "auditory_with_audio": _r(aud_full_n[t]) if t < min_len else None,
            "auditory_without_audio": _r(aud_silent_n[t]) if t < min_len else None,
        })

    eng = raw["engagement_overall"]
    raw_out = {k: [round(float(v), 6) for v in ts] for k, ts in raw.items()}
    raw_out["auditory_without_audio"] = [round(float(v), 6) for v in aud_silent]
    return {
        "duration_seconds": int(T),
        "timesteps": timesteps,
        "raw": raw_out,
        "summary": {
            "peak_t": int(np.argmax(eng)),
            "trough_t": int(np.argmin(eng)),
            "mean_engagement_raw": round(float(eng.mean()), 6),
            "audio_lift_mean_raw": round(float((aud_full[:min_len] - aud_silent[:min_len]).mean()), 6),
        },
    }
