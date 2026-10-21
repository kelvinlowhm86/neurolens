"""Pure maths: turns TRIBE v2 predictions into signed brain-response curves. numpy only.

M3d §2: seven signals in three groups, each value the signed mean over the signal's vertices
(no absolute value, no rescaling). The muted series are cut to the shared length of the two
passes. See docs/M3d_spec.md.
"""

import logging

import numpy as np

logger = logging.getLogger("neurolens")

N_VERTICES_PER_HEMISPHERE = 10242

SIGNALS = {
    "faces": ["G_oc-temp_lat-fusifor"],
    "bodies": ["S_oc-temp_lat", "G_temporal_inf"],
    "scenes": ["G_oc-temp_med-Parahip"],
    "visual": ["S_calcarine", "G_cuneus", "Pole_occipital", "G_oc-temp_med-Lingual"],
    "sound": ["G_temp_sup-G_T_transv", "G_temp_sup-Plan_tempo"],
    "speech": [
        "G_front_inf-Opercular",
        "G_front_inf-Triangul",
        "G_temporal_middle",
        "G_temp_sup-Lateral",
    ],
    "social": ["S_temporal_sup"],
}

LEFT_ONLY = {"speech"}

GROUPS = {
    "see": ["faces", "bodies", "scenes", "visual"],
    "hear": ["sound"],
    "understand": ["speech", "social"],
}

SERIES = [*SIGNALS, *GROUPS, "overall"]


def build_masks(labels_full, label_names):
    """One boolean mask per signal, matching Destrieux labels by exact name.

    Bytes names are decoded first. A target label missing from the atlas raises ValueError
    naming it: a silent near-miss would leave a signal empty and its mean NaN.
    """
    labels = np.asarray(labels_full)
    names = [name.decode() if isinstance(name, bytes) else str(name) for name in label_names]

    missing = [target for targets in SIGNALS.values() for target in targets if target not in names]
    if missing:
        raise ValueError(f"Atlas labels missing (atlas changed?): {', '.join(missing)}")

    masks = {}
    for signal, targets in SIGNALS.items():
        mask = np.zeros(len(labels), dtype=bool)
        for target in targets:
            mask |= labels == names.index(target)
        if signal in LEFT_ONLY:
            mask[N_VERTICES_PER_HEMISPHERE:] = False
        masks[signal] = mask
        logger.info(f"  signal {signal}: {mask.sum()} vertices")
    return masks


def require_nonempty_masks(masks):
    """Raise if any signal has no vertices. An empty mask averages to NaN, which would otherwise
    reach a published result (the atlas labels or their spelling changed)."""
    empty = [name for name, mask in masks.items() if not mask.any()]
    if empty:
        raise ValueError(
            f"Signal masks with no vertices (atlas labels changed?): {', '.join(empty)}"
        )


def _series(preds, masks):
    """The seven signal means, the three group means and overall, unrounded."""
    signals = {name: preds[:, mask].mean(axis=1) for name, mask in masks.items()}
    values = dict(signals)
    for group, names in GROUPS.items():
        values[group] = np.mean([signals[name] for name in names], axis=0)
    values["overall"] = np.mean([values[group] for group in GROUPS], axis=0)
    return values


def _rounded(values):
    return {name: [round(float(value), 3) for value in values[name]] for name in SERIES}


def extract_response(preds_full, preds_noaudio, masks):
    """The format-2 response: signed signal means for both passes (M3d §2-§3)."""
    full = np.asarray(preds_full)
    noaudio = np.asarray(preds_noaudio)
    length = full.shape[0]
    muted_length = min(length, noaudio.shape[0])

    return {
        "format": 2,
        "duration_seconds": int(length),
        "signals": _rounded(_series(full, masks)),
        "muted": _rounded(_series(noaudio[:muted_length], masks)),
    }
