"""infra/make_brain_mesh.py's cache check.

The script promises no network call, so every surface it reads must already be under
data/nilearn (including the fsaverage5 sphere files the parent lookup needs).
"""

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INFRA = REPO / "infra"


def load_make_brain_mesh():
    path = INFRA / "make_brain_mesh.py"
    spec = importlib.util.spec_from_file_location("make_brain_mesh", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake")


def cached_repo(tmp_path, monkeypatch):
    module = load_make_brain_mesh()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "DATA_DIR", tmp_path / "data" / "nilearn")
    for surface in ("pial", "sphere", "sulc"):
        for side in ("left", "right"):
            touch(module.DATA_DIR / "fsaverage6" / f"{surface}_{side}.gii.gz")
    for side in ("left", "right"):
        touch(module.DATA_DIR / "destrieux_surface" / f"{side}.aparc.a2009s.annot")
    return module


def test_check_cached_requires_fsaverage5_in_the_repo_cache(tmp_path, monkeypatch):
    module = cached_repo(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="fsaverage5"):
        module._check_cached()


def test_check_cached_accepts_a_complete_repo_cache(tmp_path, monkeypatch):
    module = cached_repo(tmp_path, monkeypatch)
    for side in ("left", "right"):
        touch(module.DATA_DIR / "fsaverage5" / f"sphere_{side}.gii.gz")

    module._check_cached()
