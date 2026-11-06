"""M3d §8: the committed fsaverage6 pial mesh, the head shell and their licence."""

import struct
from pathlib import Path

import numpy as np
import pytest

BRAIN_DIR = Path(__file__).resolve().parent.parent / "static" / "brain"
MESH = BRAIN_DIR / "fsaverage6_pial.bin"
HEAD = BRAIN_DIR / "head.bin"
LICENSE = BRAIN_DIR / "LICENSE.txt"
N_VERTICES = 81924
N_FSAVERAGE5 = 20484


def read_or_fail(path):
    if not path.exists():
        pytest.fail(f"{path} is missing. Create it with: python infra/make_brain_mesh.py")
    return path.read_bytes()


def read_mesh(data):
    vertex_count, face_count = struct.unpack_from("<II", data, 0)
    offset = 8
    xyz = np.frombuffer(data, dtype="<f4", count=vertex_count * 3, offset=offset)
    offset += xyz.nbytes
    faces = np.frombuffer(data, dtype="<u4", count=face_count * 3, offset=offset)
    offset += faces.nbytes
    flags = np.frombuffer(data, dtype=np.uint8, count=vertex_count, offset=offset)
    offset += flags.nbytes
    depth = np.frombuffer(data, dtype="<f4", count=vertex_count, offset=offset)
    offset += depth.nbytes
    parents = np.frombuffer(data, dtype="<u4", count=vertex_count * 2, offset=offset)
    offset += parents.nbytes
    return vertex_count, face_count, xyz, faces, flags, depth, parents, offset


def test_the_fsaverage6_pial_mesh_is_well_formed():
    data = read_or_fail(MESH)
    vertex_count, face_count, xyz, faces, flags, depth, parents, offset = read_mesh(data)

    assert offset == len(data), "the mesh file has trailing or missing bytes"
    assert vertex_count == N_VERTICES
    assert face_count > 0
    assert xyz.shape == (N_VERTICES * 3,)
    assert np.isfinite(xyz).all()
    assert faces.shape == (face_count * 3,)
    assert faces.max() < N_VERTICES
    assert flags.shape == (N_VERTICES,)
    assert set(np.unique(flags)) <= {0, 1}
    assert flags.any() and not flags.all(), "the no-cortex flags are missing"
    assert depth.shape == (N_VERTICES,)
    assert np.isfinite(depth).all()
    assert depth.min() >= -1.0 and depth.max() <= 1.0

    parents = parents.reshape(N_VERTICES, 2)
    assert parents.max() < N_FSAVERAGE5
    self_parents = parents[:, 0] == parents[:, 1]
    assert self_parents.sum() == N_FSAVERAGE5
    assert np.array_equal(np.sort(parents[self_parents, 0]), np.arange(N_FSAVERAGE5))


def test_the_head_shell_is_well_formed():
    data = read_or_fail(HEAD)
    vertex_count, face_count = struct.unpack_from("<II", data, 0)
    offset = 8
    xyz = np.frombuffer(data, dtype="<f4", count=vertex_count * 3, offset=offset)
    offset += xyz.nbytes
    faces = np.frombuffer(data, dtype="<u4", count=face_count * 3, offset=offset)
    offset += faces.nbytes

    assert offset == len(data), "the head file has trailing or missing bytes"
    assert vertex_count > 0
    assert face_count > 0
    assert xyz.shape == (vertex_count * 3,)
    assert np.isfinite(xyz).all()
    assert faces.shape == (face_count * 3,)
    assert faces.max() < vertex_count


def test_the_head_licence_notice_is_committed_and_non_empty():
    data = read_or_fail(LICENSE)
    assert data.strip()
