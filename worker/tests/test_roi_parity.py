"""The ported ROI code must give exactly the prototype's numbers (legacy app.py, Sept 2026)."""

import numpy as np

from neurolens_worker import roi

ROI_LABEL_MAP = roi.ROI_LABEL_MAP


# ─── Verbatim logic from app.py (module globals turned into arguments) ──────


def legacy_masks(labels_full, label_names):
    roi_masks = {}
    for roi_name, target_labels in ROI_LABEL_MAP.items():
        mask = np.zeros(len(labels_full), dtype=bool)
        for target in target_labels:
            for i, name in enumerate(label_names):
                if target in name:
                    mask |= (labels_full == i)
        roi_masks[roi_name] = mask
    return roi_masks


def legacy_normalize_01(arr):
    mn, mx = arr.min(), arr.max()
    if mx - mn < 1e-8:
        return np.zeros_like(arr)
    return (arr - mn) / (mx - mn)


def legacy_extract_engagement(preds_full, preds_noaudio, roi_masks):
    T = preds_full.shape[0]
    roi_timeseries = {}
    for roi_name, mask in roi_masks.items():
        roi_timeseries[roi_name] = np.abs(preds_full[:, mask]).mean(axis=1)
    roi_timeseries["engagement_overall"] = np.mean([roi_timeseries[k] for k in ROI_LABEL_MAP.keys()], axis=0)
    roi_normed = {name: legacy_normalize_01(ts) for name, ts in roi_timeseries.items()}
    aud_mask = roi_masks["auditory"]
    aud_full = np.abs(preds_full[:, aud_mask]).mean(axis=1)
    aud_noaudio = np.abs(preds_noaudio[:, aud_mask]).mean(axis=1)
    min_len = min(len(aud_full), len(aud_noaudio))
    aud_full = aud_full[:min_len]
    aud_noaudio = aud_noaudio[:min_len]
    aud_full_normed = legacy_normalize_01(aud_full)
    aud_noaudio_normed = legacy_normalize_01(aud_noaudio)
    timesteps = []
    for t in range(T):
        timesteps.append({
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
        })
    return {"duration_seconds": int(T), "timesteps": timesteps}


# ─── Fixtures: a fake atlas whose label names contain the real Destrieux substrings ──

LABEL_NAMES = ["Unknown", "G_oc-temp_lat-fusifor", "S_oc-temp_lat", "G_temporal_inf",
               "G_oc-temp_med-Parahip", "S_temporal_sup", "G_temp_sup-G_T_transv",
               "G_temp_sup-Plan_tempo", "G_front_sup"]


def _labels(seed=0):
    return np.random.default_rng(seed).integers(0, len(LABEL_NAMES), roi.N_VERTICES)


def test_masks_match_legacy():
    labels = _labels()
    new, old = roi.build_masks(labels, LABEL_NAMES), legacy_masks(labels, LABEL_NAMES)
    assert new.keys() == old.keys()
    for k in new:
        assert np.array_equal(new[k], old[k])


def test_extract_matches_legacy_including_shorter_silent_pass():
    rng = np.random.default_rng(1)
    masks = roi.build_masks(_labels(), LABEL_NAMES)
    full = rng.standard_normal((28, roi.N_VERTICES)).astype(np.float32)
    silent = rng.standard_normal((27, roi.N_VERTICES)).astype(np.float32)   # silent pass one TR short
    new = roi.extract(full, silent, masks)
    old = legacy_extract_engagement(full, silent, masks)
    assert new["duration_seconds"] == old["duration_seconds"]
    assert new["timesteps"] == old["timesteps"]
    assert new["timesteps"][-1]["auditory_without_audio"] is None
    assert len(new["raw"]["ffa_faces"]) == 28
    assert 0 <= new["summary"]["peak_t"] < 28
