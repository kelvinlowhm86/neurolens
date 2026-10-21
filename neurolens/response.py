"""Pure maths: turns TRIBE v2 predictions into signed brain-response curves. numpy only.

M3d §2: seven signals over the Glasser HCP-MMP1 areas, each value the signed mean over the
signal's vertices (no absolute value, no rescaling). The muted series are cut to the shared
length of the two passes. See docs/M3d_spec.md.
"""

import hashlib
import logging

import numpy as np

logger = logging.getLogger("neurolens")

N_VERTICES_PER_HEMISPHERE = 10242

SIGNALS = {
    "faces": ["FFC"],
    "bodies": ["V4t"],
    "scenes": ["PHA1", "PHA2", "PHA3"],
    "visual": ["V1", "V2", "V3"],
    "sound": ["A1", "LBelt", "MBelt", "PBelt"],
    "speech": ["A5", "STSva", "STSvp", "45"],
    "social": ["PGi", "TE1a"],
}

LEFT_ONLY = {"speech"}

GROUPS = {
    "see": ["faces", "bodies", "scenes", "visual"],
    "hear": ["sound"],
    "understand": ["speech", "social"],
}

SERIES = [*SIGNALS, *GROUPS, "overall"]

HCP_MMP_FILES = {
    "lh": (
        "lh.HCPMMP1.annot",
        "https://ndownloader.figshare.com/files/5528816",
        "d4da634644b4c595dbda23963e01752059f0e7714be70169eb84e25e09ba2b44",
    ),
    "rh": (
        "rh.HCPMMP1.annot",
        "https://ndownloader.figshare.com/files/5528819",
        "744eff4e57ce8121c43851eea425475baf92d9ba8686299a7435517ab972e9a2",
    ),
}


def _normalise_name(name):
    """Drop the hemisphere prefix and the `_ROI` suffix from an HCP-MMP1 label name."""
    if isinstance(name, bytes):
        name = name.decode()
    else:
        name = str(name)
    if name.startswith(("L_", "R_")):
        name = name[2:]
    if name.endswith("_ROI"):
        name = name[: -len("_ROI")]
    return name


def build_masks(lh_labels, lh_names, rh_labels, rh_names):
    """One boolean mask per signal over fsaverage5's left then right vertices.

    Labels and names come from the Glasser HCP-MMP1 annot files. Each hemisphere's label array
    is cut to its first 10,242 entries (fsaverage5). Bytes names are decoded and normalised by
    dropping the leading `L_`/`R_` and trailing `_ROI`; only an exact match counts, and a target
    missing from either hemisphere raises ValueError naming it and that hemisphere.
    """
    lh_labels = np.asarray(lh_labels)[:N_VERTICES_PER_HEMISPHERE]
    rh_labels = np.asarray(rh_labels)[:N_VERTICES_PER_HEMISPHERE]
    hemispheres = (
        ("left", lh_labels, [_normalise_name(name) for name in lh_names]),
        ("right", rh_labels, [_normalise_name(name) for name in rh_names]),
    )

    for targets in SIGNALS.values():
        for target in targets:
            for side, _, names in hemispheres:
                if target not in names:
                    raise ValueError(f"Missing HCP-MMP1 label {target!r} in the {side} hemisphere.")

    masks = {}
    for signal, targets in SIGNALS.items():
        left = np.zeros_like(lh_labels, dtype=bool)
        right = np.zeros_like(rh_labels, dtype=bool)
        for target in targets:
            left |= lh_labels == hemispheres[0][2].index(target)
            if signal not in LEFT_ONLY:
                right |= rh_labels == hemispheres[1][2].index(target)
        mask = np.concatenate([left, right])
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


def verify_sha256(path, expected):
    """Raise ValueError unless `path` hashes to `expected`; return None on a match."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise ValueError(f"{path} does not match its pinned SHA-256 (the map file changed).")


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
