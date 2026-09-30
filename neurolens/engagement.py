"""Pure maths: turns TRIBE v2 predictions into per-region engagement curves. numpy only.

Normalisation rule (do not change): extract_engagement cuts the with-audio and no-audio
auditory passes to their shared length FIRST, then rescales each to 0-1. The five main ROI
columns are rescaled over the full timeline. So `auditory` and `auditory_with_audio` may
differ in the same row. See AGENTS.md.
"""

import logging

import numpy as np

logger = logging.getLogger("neurolens")

ROI_LABEL_MAP = {
    "ffa_faces": ["G_oc-temp_lat-fusifor"],
    "eba_bodies": ["S_oc-temp_lat", "G_temporal_inf"],
    "ppa_scenes": ["G_oc-temp_med-Parahip"],
    "sts_social": ["S_temporal_sup"],
    "auditory": ["G_temp_sup-G_T_transv", "G_temp_sup-Plan_tempo"],
}


def normalize_01(arr):
    mn, mx = arr.min(), arr.max()
    if mx - mn < 1e-8:
        return np.zeros_like(arr)
    return (arr - mn) / (mx - mn)


def build_roi_masks(labels_full, label_names, roi_label_map=ROI_LABEL_MAP):
    """One boolean mask per ROI. A label matches when the target text is in the label name."""
    roi_masks = {}
    for roi_name, target_labels in roi_label_map.items():
        mask = np.zeros(len(labels_full), dtype=bool)
        for target in target_labels:
            for i, name in enumerate(label_names):
                if target in name:
                    mask |= labels_full == i
        roi_masks[roi_name] = mask
        logger.info(f"  ROI {roi_name}: {mask.sum()} vertices")
    return roi_masks


def extract_engagement(preds_full, preds_noaudio, roi_masks):
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
            "auditory_without_audio": (
                round(float(aud_noaudio_normed[t]), 4) if t < min_len else None
            ),
        }
        timesteps.append(step)

    return {
        "duration_seconds": int(T),
        "timesteps": timesteps,
    }
