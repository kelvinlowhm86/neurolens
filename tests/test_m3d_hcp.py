"""M3d §2: the Glasser HCP-MMP1 signal masks and the map files' integrity.

No network call: the annot round-trip writes a small synthetic file with nibabel.
"""

import hashlib
import re

import numpy as np
import pytest


def response():
    """Imported lazily so the rest of the suite still collects before the M3d code exists."""
    import neurolens.response as module

    return module


SIGNALS = {
    "faces": ["FFC"],
    "bodies": ["V4t"],
    "scenes": ["PHA1", "PHA2", "PHA3"],
    "visual": ["V1", "V2", "V3"],
    "sound": ["A1", "LBelt", "MBelt", "PBelt"],
    "speech": ["A5", "STSva", "STSvp", "45"],
    "social": ["PGi", "TE1a"],
}
AREAS = [area for targets in SIGNALS.values() for area in targets]
N = 10242


def names_for(hemisphere, areas=AREAS):
    return [f"{hemisphere}_{area}_ROI".encode() for area in areas]


def labels_for(names, block=10):
    labels = np.full(N, -1, dtype=int)
    for i in range(len(names)):
        labels[i * block : (i + 1) * block] = i
    return labels


def expected_mask(signal):
    mask = np.zeros(2 * N, dtype=bool)
    for hemisphere, offset in (("L", 0), ("R", N)):
        if signal in response().LEFT_ONLY and hemisphere == "R":
            continue
        for area in SIGNALS[signal]:
            i = AREAS.index(area)
            mask[offset + i * 10 : offset + (i + 1) * 10] = True
    return mask


# ---------------------------------------------------------------- the signal table


def test_signals_are_the_spec_table():
    assert response().SIGNALS == SIGNALS
    assert list(response().SIGNALS) == list(SIGNALS)


def test_left_only_groups_and_series_are_unchanged():
    assert response().LEFT_ONLY == {"speech"}
    assert response().GROUPS == {
        "see": ["faces", "bodies", "scenes", "visual"],
        "hear": ["sound"],
        "understand": ["speech", "social"],
    }
    assert response().SERIES == [
        *SIGNALS,
        "see",
        "hear",
        "understand",
        "overall",
    ]


# ---------------------------------------------------------------- build_masks


def test_masks_match_the_normalised_hcp_names():
    lh_names = names_for("L")
    rh_names = names_for("R")
    masks = response().build_masks(labels_for(lh_names), lh_names, labels_for(rh_names), rh_names)

    assert set(masks) == set(SIGNALS)
    for signal in SIGNALS:
        np.testing.assert_array_equal(masks[signal], expected_mask(signal))
        assert masks[signal].any()


def test_names_normalise_from_bytes_and_str():
    lh_names = names_for("L")
    rh_names = names_for("R")
    rh_names[0] = "R_FFC_ROI"
    masks = response().build_masks(labels_for(lh_names), lh_names, labels_for(rh_names), rh_names)

    assert masks["faces"][:10].all()
    assert masks["faces"][N : N + 10].all()


def test_exact_names_match_and_near_misses_do_not():
    lh_names = names_for("L") + [
        b"L_V1x_ROI",
        b"L_MV1_ROI",
        b"L_p45_ROI",
    ]
    rh_names = names_for("R") + [
        b"R_V1x_ROI",
        b"R_MV1_ROI",
        b"R_p45_ROI",
    ]
    masks = response().build_masks(labels_for(lh_names), lh_names, labels_for(rh_names), rh_names)

    for near_miss in (b"L_V1x_ROI", b"L_MV1_ROI", b"L_p45_ROI"):
        i = lh_names.index(near_miss)
        assert not masks["visual"][i * 10 : (i + 1) * 10].any()
        assert not masks["speech"][i * 10 : (i + 1) * 10].any()
    for near_miss in (b"R_V1x_ROI", b"R_MV1_ROI", b"R_p45_ROI"):
        i = rh_names.index(near_miss)
        start = N + i * 10
        assert not masks["visual"][start : start + 10].any()
        assert not masks["speech"][start : start + 10].any()


@pytest.mark.parametrize("hemisphere", ["lh", "rh"])
def test_a_target_missing_from_either_hemisphere_raises_and_names_it(hemisphere):
    lh_names = names_for("L")
    rh_names = names_for("R")
    if hemisphere == "lh":
        del lh_names[AREAS.index("FFC")]
    else:
        del rh_names[AREAS.index("FFC")]

    with pytest.raises(ValueError) as err:
        response().build_masks(labels_for(lh_names), lh_names, labels_for(rh_names), rh_names)

    assert "FFC" in str(err.value)


def test_each_hemisphere_is_cut_to_its_first_10242_labels():
    lh_names = names_for("L")
    rh_names = names_for("R")
    lh_labels = np.full(N + 3, -1, dtype=int)
    lh_labels[N - 1] = 0
    lh_labels[N] = 0
    rh_labels = np.full(N + 3, -1, dtype=int)
    rh_labels[0] = 0
    rh_labels[N] = 0

    masks = response().build_masks(lh_labels, lh_names, rh_labels, rh_names)

    faces = masks["faces"]
    assert len(faces) == 2 * N
    assert faces.sum() == 2
    assert faces[N - 1]
    assert faces[N]


def test_speech_is_left_hemisphere_only_even_when_the_right_map_has_the_areas():
    lh_names = names_for("L")
    rh_names = names_for("R")
    masks = response().build_masks(labels_for(lh_names), lh_names, labels_for(rh_names), rh_names)

    assert masks["speech"][:N].any()
    assert not masks["speech"][N:].any()


def test_masks_are_disjoint_and_none_empty():
    lh_names = names_for("L")
    rh_names = names_for("R")
    masks = response().build_masks(labels_for(lh_names), lh_names, labels_for(rh_names), rh_names)

    assert all(mask.any() for mask in masks.values())
    names = list(masks)
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            assert not (masks[left] & masks[right]).any(), (left, right)


def test_require_nonempty_masks_names_every_empty_signal():
    masks = {
        "faces": np.array([True, False]),
        "bodies": np.array([False, False]),
        "scenes": np.array([True, True]),
        "sound": np.array([False, False]),
    }
    with pytest.raises(ValueError) as err:
        response().require_nonempty_masks(masks)
    assert "bodies" in str(err.value)
    assert "sound" in str(err.value)
    assert "faces" not in str(err.value)
    assert "scenes" not in str(err.value)


# ---------------------------------------------------------------- map files


def test_hcp_mmp_files_are_pinned_by_figshare_id_and_sha256():
    files = response().HCP_MMP_FILES
    assert set(files) == {"lh", "rh"}

    lh_name, lh_url, lh_sha = files["lh"]
    rh_name, rh_url, rh_sha = files["rh"]

    assert lh_name == "lh.HCPMMP1.annot"
    assert rh_name == "rh.HCPMMP1.annot"
    assert "5528816" in lh_url
    assert "5528819" in rh_url
    for sha in (lh_sha, rh_sha):
        assert re.fullmatch(r"[0-9a-f]{64}", sha)
    assert lh_sha.startswith("d4da6346") and lh_sha.endswith("2b44")
    assert rh_sha.startswith("744eff4e") and rh_sha.endswith("e9a2")


def test_verify_sha256_accepts_a_matching_file_and_names_a_mismatch(tmp_path):
    path = tmp_path / "lh.HCPMMP1.annot"
    path.write_bytes(b"a small stand-in for the map")
    expected = hashlib.sha256(path.read_bytes()).hexdigest()

    assert response().verify_sha256(path, expected) is None

    with pytest.raises(ValueError) as err:
        response().verify_sha256(path, "0" * 64)
    assert path.name in str(err.value)


# ---------------------------------------------------------------- reading the annot files


def _write_annot(path, names, labels):
    import nibabel.freesurfer as freesurfer

    ctab = np.array(
        [[10 * (i + 1), 0, 0, 0] for i in range(len(names))],
        dtype=np.int32,
    )
    freesurfer.write_annot(str(path), labels, ctab=ctab, names=names)
    return path


def test_read_hcp_annot_returns_labels_and_names_that_build_the_masks(tmp_path):
    lh_names = names_for("L")
    rh_names = names_for("R")
    lh_path = _write_annot(tmp_path / "lh.HCPMMP1.annot", lh_names, labels_for(lh_names))
    rh_path = _write_annot(tmp_path / "rh.HCPMMP1.annot", rh_names, labels_for(rh_names))

    import neurolens.inference as inference

    lh_labels, lh_read_names = inference.read_hcp_annot(lh_path)
    rh_labels, rh_read_names = inference.read_hcp_annot(rh_path)
    masks = response().build_masks(lh_labels, lh_read_names, rh_labels, rh_read_names)

    assert len(lh_labels) == len(rh_labels) == N
    assert set(lh_read_names) == set(lh_names)
    for signal in SIGNALS:
        np.testing.assert_array_equal(masks[signal], expected_mask(signal))
