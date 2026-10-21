#!/usr/bin/env python3
"""Make static/brain/fsaverage5_inflated.bin for the page's 3D brain (M3d §8).

Layout, little-endian:
    uint32 vertex count (20484)
    uint32 face count
    float32 xyz per vertex (left hemisphere vertices first, then right)
    uint32 three vertex indices per face
    uint8 per vertex (1 = no cortex: Destrieux Medial_wall or Unknown)

Uses only the fsaverage5 inflated surfaces bundled with nilearn and the Destrieux atlas already in
data/nilearn. It makes no network call: stop if the atlas is not cached.
"""

import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
ATLAS_DIR = ROOT / "data" / "nilearn" / "destrieux_surface"
OUTPUT = ROOT / "static" / "brain" / "fsaverage5_inflated.bin"
N_VERTICES = 20484
HEMISPHERE_VERTICES = 10242
NO_CORTEX = {"Medial_wall", "Unknown"}
GAP = 10.0


def _check_atlas_is_cached():
    annotations = ["left.aparc.a2009s.annot", "right.aparc.a2009s.annot"]
    missing = [name for name in annotations if not (ATLAS_DIR / name).exists()]
    if missing:
        raise RuntimeError(
            f"The Destrieux atlas is not cached in {ATLAS_DIR} (missing {', '.join(missing)}). "
            "This script makes no network call: stop and fetch the atlas deliberately first."
        )


def main():
    _check_atlas_is_cached()

    from nilearn import datasets, surface

    fsaverage = datasets.fetch_surf_fsaverage("fsaverage5")
    atlas = datasets.fetch_atlas_surf_destrieux(data_dir=str(ROOT / "data" / "nilearn"), verbose=0)

    meshes = {hemi: surface.load_surf_mesh(fsaverage[f"infl_{hemi}"]) for hemi in ("left", "right")}
    for hemi, mesh in meshes.items():
        if mesh.coordinates.shape != (HEMISPHERE_VERTICES, 3):
            raise ValueError(f"{hemi} inflated surface has {len(mesh.coordinates)} vertices")

    all_x = np.concatenate([mesh.coordinates[:, 0] for mesh in meshes.values()])
    width = float(all_x.max() - all_x.min())
    offsets = {
        "left": -(width + GAP) / 2,
        "right": +(width + GAP) / 2,
    }

    coordinates = []
    faces = []
    medial = []
    base = 0
    for hemi, mesh in meshes.items():
        xyz = np.asarray(mesh.coordinates, dtype=np.float64).copy()
        xyz[:, 0] = xyz[:, 0] - float(xyz[:, 0].mean()) + offsets[hemi]
        coordinates.append(xyz.astype("<f4"))
        faces.append(np.asarray(mesh.faces, dtype="<u4") + base)
        label_ids = atlas[f"map_{hemi}"]
        names = np.array(atlas["labels"], dtype=object)[label_ids]
        medial.append(np.isin(names, list(NO_CORTEX)).astype(np.uint8))
        base += len(xyz)

    xyz = np.concatenate(coordinates)
    face_array = np.concatenate(faces)
    medial_array = np.concatenate(medial)

    if len(xyz) != N_VERTICES:
        raise ValueError(f"expected {N_VERTICES} vertices, got {len(xyz)}")
    if face_array.max() >= N_VERTICES:
        raise ValueError("a face index is outside the vertex range")
    if not medial_array.any() or medial_array.all():
        raise ValueError("the no-cortex flags look wrong (none, or every vertex)")

    data = struct.pack("<II", N_VERTICES, len(face_array))
    data += xyz.tobytes(order="C")
    data += face_array.tobytes(order="C")
    data += medial_array.tobytes(order="C")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(data)
    print(
        f"Wrote {OUTPUT} ({len(data)} bytes, {len(xyz)} vertices, "
        f"{len(face_array)} faces, {int(medial_array.sum())} no-cortex vertices)"
    )


if __name__ == "__main__":
    sys.exit(main())
