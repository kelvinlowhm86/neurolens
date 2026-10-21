#!/usr/bin/env python3
"""Make the page's 3D brain mesh and head shell (M3d §8).

static/brain/fsaverage6_pial.bin, little-endian:
    uint32 vertex count (81924)
    uint32 face count
    float32 xyz per vertex (fsaverage6 left pial vertices, then right; no offsets, the pial
        hemispheres do not overlap)
    uint32 three vertex indices per face (right faces offset by 40962)
    uint8 per vertex (1 = no cortex: either parent is Destrieux Medial_wall or Unknown)
    float32 sulcal depth per vertex, divided by its maximum absolute value (+-1)
    uint32 two parent indices per vertex (fsaverage5, right hemisphere + 10242; an fsaverage5
        vertex is its own parent twice)

fsaverage6 is fsaverage5 with every triangle split once: its first 10,242 vertices are the
fsaverage5 vertices, and each other vertex is the midpoint of an fsaverage5 edge. The parents are
found from the sphere surfaces: the nearest fsaverage5 edge midpoint is the vertex's edge; the
script checks that every edge is used once and that each midpoint is about equidistant from its
two parents.

static/brain/head.bin, little-endian:
    uint32 vertex count
    uint32 face count
    float32 xyz per vertex, in MNI millimetres
    uint32 three vertex indices per face
    nothing after the faces

The head is the outer surface of the MNI152 2009 template bundled with nilearn (no download),
extracted with scikit-image and reduced to its largest connected component. Its permissive
licence notice is static/brain/LICENSE.txt.

Uses the fsaverage6 and Destrieux data already in data/nilearn. It makes no network call: if
either is missing, it stops and tells the operator to fetch it.
"""

import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "nilearn"
BRAIN_DIR = ROOT / "static" / "brain"
MESH_OUTPUT = BRAIN_DIR / "fsaverage6_pial.bin"
HEAD_OUTPUT = BRAIN_DIR / "head.bin"
HEMISPHERES = ("left", "right")
N_FSAVERAGE5 = 10242
N_FSAVERAGE6 = 40962
N_VERTICES = 2 * N_FSAVERAGE6
NO_CORTEX = {"Medial_wall", "Unknown"}
HEAD_THRESHOLD_FRACTION = 0.1
HEAD_STEP_SIZE = 3


def _check_cached():
    required = [
        DATA_DIR / "fsaverage6" / f"{surface}_{side}.gii.gz"
        for surface in ("pial", "sphere", "sulc")
        for side in HEMISPHERES
    ]
    required += [
        DATA_DIR / "destrieux_surface" / "left.aparc.a2009s.annot",
        DATA_DIR / "destrieux_surface" / "right.aparc.a2009s.annot",
    ]
    missing = [path for path in required if not path.exists()]
    if missing:
        names = ", ".join(str(path.relative_to(ROOT)) for path in missing)
        raise RuntimeError(
            f"Required data is not cached (missing {names}). This script makes no network call: "
            'fetch it first with nilearn.datasets.fetch_surf_fsaverage("fsaverage6", '
            'data_dir="data/nilearn") and fetch_atlas_surf_destrieux(data_dir="data/nilearn").'
        )


def _edges(faces):
    pairs = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    return np.unique(np.sort(pairs, axis=1), axis=0)


def _hemisphere_parents(fs5, fs6, side):
    from nilearn import surface
    from scipy.spatial import cKDTree

    mesh5 = surface.load_surf_mesh(fs5[f"sphere_{side}"])
    sphere5 = np.asarray(mesh5.coordinates)
    sphere6 = np.asarray(surface.load_surf_mesh(fs6[f"sphere_{side}"]).coordinates)
    faces5 = np.asarray(mesh5.faces, dtype=int)
    if sphere5.shape != (N_FSAVERAGE5, 3) or sphere6.shape != (N_FSAVERAGE6, 3):
        raise ValueError(f"{side} sphere has the wrong vertex count")

    if not np.allclose(sphere5, sphere6[:N_FSAVERAGE5], atol=1e-4, rtol=0):
        raise ValueError(
            f"{side}: fsaverage6's first {N_FSAVERAGE5} sphere vertices are not fsaverage5's"
        )

    normal5 = sphere5 / np.linalg.norm(sphere5, axis=1, keepdims=True)
    normal6 = sphere6 / np.linalg.norm(sphere6, axis=1, keepdims=True)
    edges = _edges(faces5)
    if len(edges) != N_FSAVERAGE6 - N_FSAVERAGE5:
        raise ValueError(f"{side}: expected {N_FSAVERAGE6 - N_FSAVERAGE5} edges, got {len(edges)}")

    midpoints = (normal5[edges[:, 0]] + normal5[edges[:, 1]]) / 2
    midpoints /= np.linalg.norm(midpoints, axis=1, keepdims=True)
    nearest = cKDTree(midpoints).query(normal6[N_FSAVERAGE5:])[1]
    used = np.bincount(nearest, minlength=len(edges))
    if not np.all(used == 1):
        raise ValueError(f"{side}: an fsaverage5 edge was not matched to exactly one vertex")

    parents = edges[nearest]
    first = sphere6[N_FSAVERAGE5:]
    distance0 = np.linalg.norm(first - sphere5[parents[:, 0]], axis=1)
    distance1 = np.linalg.norm(first - sphere5[parents[:, 1]], axis=1)
    edge_length = np.linalg.norm(sphere5[parents[:, 0]] - sphere5[parents[:, 1]], axis=1)
    relative = np.abs(distance0 - distance1) / edge_length
    if relative.max() >= 0.01:
        raise ValueError(
            f"{side}: a midpoint is not equidistant from its parents "
            f"(relative difference {relative.max():.3%})"
        )

    all_parents = np.empty((N_FSAVERAGE6, 2), dtype=np.uint32)
    all_parents[:N_FSAVERAGE5, 0] = np.arange(N_FSAVERAGE5)
    all_parents[:N_FSAVERAGE5, 1] = np.arange(N_FSAVERAGE5)
    all_parents[N_FSAVERAGE5:] = parents
    return all_parents


def _head_mesh():
    from nilearn import datasets
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from skimage import measure

    image = datasets.load_mni152_template(resolution=1)
    volume = np.asarray(image.dataobj, dtype=np.float32)
    level = float(np.percentile(volume, 99) * HEAD_THRESHOLD_FRACTION)
    if level <= 0:
        raise ValueError("the MNI152 template has no head above background")
    print(f"Head threshold: {level:.6f} ({HEAD_THRESHOLD_FRACTION} of the 99th percentile)")

    vertices, faces, _, _ = measure.marching_cubes(volume, level=level, step_size=HEAD_STEP_SIZE)

    rows = np.concatenate([faces[:, 0], faces[:, 1], faces[:, 2]])
    columns = np.concatenate([faces[:, 1], faces[:, 2], faces[:, 0]])
    graph = coo_matrix(
        (np.ones(len(rows), dtype=np.uint8), (rows, columns)),
        shape=(len(vertices), len(vertices)),
    ).tocsr()
    _, labels = connected_components(graph, directed=False)
    keep = int(np.argmax(np.bincount(labels)))
    vertex_count = len(vertices)
    vertices = vertices[labels == keep]
    faces = faces[np.all(labels[faces] == keep, axis=1)]

    used = np.unique(faces)
    remap = np.full(vertex_count, -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    faces = remap[faces]
    vertices = vertices[used]

    affine = np.asarray(image.affine)
    world = vertices @ affine[:3, :3].T + affine[:3, 3]
    return world, faces


def _write_mesh(path, coordinates, faces, extra=b""):
    data = struct.pack("<II", len(coordinates), len(faces))
    data += np.asarray(coordinates, dtype="<f4").tobytes(order="C")
    data += np.asarray(faces, dtype="<u4").tobytes(order="C")
    data += extra
    path.write_bytes(data)
    print(f"Wrote {path} ({len(data)} bytes, {len(coordinates)} vertices, {len(faces)} faces)")
    return data


def main():
    _check_cached()

    from nilearn import datasets, surface

    fs5 = datasets.fetch_surf_fsaverage("fsaverage5")
    fs6 = datasets.fetch_surf_fsaverage("fsaverage6", data_dir=str(DATA_DIR))
    atlas = datasets.fetch_atlas_surf_destrieux(data_dir=str(DATA_DIR), verbose=0)
    label_names = np.array(atlas["labels"], dtype=object)

    coordinates = []
    faces = []
    flags = []
    depth = []
    parents = []
    vertex_offset = 0
    for hemisphere_index, side in enumerate(HEMISPHERES):
        hemisphere_parents = _hemisphere_parents(fs5, fs6, side)
        labels = np.asarray(atlas[f"map_{side}"])
        no_cortex = np.isin(label_names[labels], list(NO_CORTEX))
        hemisphere_flags = no_cortex[hemisphere_parents[:, 0]] | no_cortex[hemisphere_parents[:, 1]]

        mesh = surface.load_surf_mesh(fs6[f"pial_{side}"])
        pial = np.asarray(mesh.coordinates, dtype=float)
        hemisphere_faces = np.asarray(mesh.faces, dtype="<u4")
        sulc = np.asarray(surface.load_surf_data(fs6[f"sulc_{side}"]), dtype=float)

        coordinates.append(pial)
        faces.append(hemisphere_faces + vertex_offset)
        flags.append(hemisphere_flags.astype(np.uint8))
        depth.append(sulc)
        parents.append(hemisphere_parents + N_FSAVERAGE5 * hemisphere_index)
        vertex_offset += len(pial)

    coordinates = np.concatenate(coordinates)
    faces = np.concatenate(faces).astype("<u4")
    flags = np.concatenate(flags)
    depth = np.concatenate(depth)
    parents = np.concatenate(parents).astype("<u4")

    if len(coordinates) != N_VERTICES:
        raise ValueError(f"expected {N_VERTICES} vertices, got {len(coordinates)}")
    if faces.max() >= N_VERTICES:
        raise ValueError("a face index is outside the vertex range")
    if not flags.any() or flags.all():
        raise ValueError("the no-cortex flags look wrong (none, or every vertex)")
    if parents.max() >= 2 * N_FSAVERAGE5:
        raise ValueError("a parent index is outside fsaverage5")
    self_parents = parents[:, 0] == parents[:, 1]
    if self_parents.sum() != 2 * N_FSAVERAGE5:
        raise ValueError("not every fsaverage5 vertex is its own parent")

    maximum = float(np.max(np.abs(depth)))
    if maximum < 1e-6:
        raise ValueError("the sulcal depth is all zero")
    depth = depth / maximum

    brain_extra = (
        flags.tobytes(order="C")
        + depth.astype("<f4").tobytes(order="C")
        + parents.tobytes(order="C")
    )
    BRAIN_DIR.mkdir(parents=True, exist_ok=True)
    _write_mesh(MESH_OUTPUT, coordinates, faces, brain_extra)

    head_coordinates, head_faces = _head_mesh()
    if not (
        (head_coordinates.min(axis=0) <= coordinates.min(axis=0)).all()
        and (head_coordinates.max(axis=0) >= coordinates.max(axis=0)).all()
    ):
        raise ValueError("the head does not enclose the brain")
    _write_mesh(HEAD_OUTPUT, head_coordinates, head_faces)


if __name__ == "__main__":
    sys.exit(main())
