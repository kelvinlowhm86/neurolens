"""M3d §8: the committed fsaverage5 mesh parses and has the layout the page decodes."""

import struct
from pathlib import Path

import numpy as np
import pytest

MESH = Path(__file__).resolve().parent.parent / "static" / "brain" / "fsaverage5_inflated.bin"
N_VERTICES = 20484


def test_the_inflated_mesh_is_well_formed():
    if not MESH.exists():
        pytest.fail(f"{MESH} is missing. Step 3 creates it with: python infra/make_brain_mesh.py")
    data = MESH.read_bytes()
    vertex_count, face_count = struct.unpack_from("<II", data, 0)
    offset = 8

    xyz = np.frombuffer(data, dtype="<f4", count=vertex_count * 3, offset=offset)
    offset += xyz.nbytes
    faces = np.frombuffer(data, dtype="<u4", count=face_count * 3, offset=offset)
    offset += faces.nbytes
    medial = np.frombuffer(data, dtype=np.uint8, count=vertex_count, offset=offset)
    offset += medial.nbytes

    assert vertex_count == N_VERTICES
    assert face_count > 0
    assert offset == len(data), "the mesh file has trailing or missing bytes"
    assert xyz.shape == (N_VERTICES * 3,)
    assert np.isfinite(xyz).all()
    assert faces.shape == (face_count * 3,)
    assert faces.max() < N_VERTICES
    assert medial.shape == (N_VERTICES,)
    assert set(np.unique(medial)) <= {0, 1}
    assert medial.any() and not medial.all(), "the medial-wall flags are missing"
