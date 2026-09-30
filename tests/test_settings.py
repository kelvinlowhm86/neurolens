"""Tests for neurolens.settings. Written from docs/M0_spec.md."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from neurolens import settings

REPO_ROOT = Path(__file__).resolve().parent.parent

ENV_VARS = [
    "NEUROLENS_ROOT",
    "HF_TOKEN",
    "HF_HOME",
    "HF_HUB_CACHE",
    "HUGGINGFACE_HUB_CACHE",
    "HF_ASSETS_CACHE",
    "HF_DATASETS_CACHE",
    "HF_XET_CACHE",
    "TORCH_HOME",
    "NILEARN_DATA",
    "HF_HUB_DOWNLOAD_TIMEOUT",
    "HF_HUB_HTTP_TIMEOUT",
]


@pytest.fixture
def sample_cfg():
    return json.loads((REPO_ROOT / "config.sample.json").read_text())


@pytest.fixture
def clean_env(monkeypatch):
    """Register every env var we might touch with monkeypatch so the real env is restored."""
    for name in ENV_VARS:
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)
    # configure_env refuses to run if huggingface_hub was already imported
    monkeypatch.delitem(sys.modules, "huggingface_hub", raising=False)
    return monkeypatch


def test_get_root_uses_env_override(clean_env, tmp_path):
    clean_env.setenv("NEUROLENS_ROOT", str(tmp_path))
    assert Path(settings.get_root()).resolve() == tmp_path.resolve()


def test_get_root_defaults_to_repo_root(clean_env):
    assert Path(settings.get_root()).resolve() == REPO_ROOT


def test_load_config_reads_config_json_from_root(tmp_path, sample_cfg):
    (tmp_path / "config.json").write_text(json.dumps(sample_cfg))
    assert settings.load_config(root=tmp_path) == sample_cfg


def test_load_config_uses_env_root_when_no_root_given(clean_env, tmp_path, sample_cfg):
    (tmp_path / "config.json").write_text(json.dumps(sample_cfg))
    clean_env.setenv("NEUROLENS_ROOT", str(tmp_path))
    assert settings.load_config() == sample_cfg


def test_load_config_missing_file_raises_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match=r"config\.sample\.json"):
        settings.load_config(root=tmp_path)


def test_missing_config_is_an_error_only_when_requested(tmp_path):
    """Importing settings and using the path helpers must not need config.json."""
    code = (
        "import neurolens.settings as s; "
        "s.get_root(); "
        "s.resolve_paths({'paths': {'models': './models', 'data': './data', "
        "'output': './output'}}, s.get_root()); "
        "print('ok')"
    )
    env = {**os.environ, "NEUROLENS_ROOT": str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


def test_resolve_paths_with_sample_config(tmp_path, sample_cfg):
    paths = settings.resolve_paths(sample_cfg, tmp_path)
    assert set(paths) == {"models", "data", "output"}
    assert Path(paths["models"]).resolve() == (tmp_path / "models").resolve()
    assert Path(paths["data"]).resolve() == (tmp_path / "data").resolve()
    assert Path(paths["output"]).resolve() == (tmp_path / "output").resolve()


def test_resolve_paths_creates_nothing(tmp_path, sample_cfg):
    settings.resolve_paths(sample_cfg, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_ensure_dirs_creates_the_three_folders(tmp_path, sample_cfg):
    paths = settings.resolve_paths(sample_cfg, tmp_path)
    settings.ensure_dirs(paths)
    for name in ("models", "data", "output"):
        assert (tmp_path / name).is_dir()


def test_configure_env_sets_hf_home_and_nilearn_data(clean_env, tmp_path, sample_cfg):
    paths = settings.resolve_paths(sample_cfg, tmp_path)
    settings.configure_env(sample_cfg, paths)
    assert Path(os.environ["HF_HOME"]).resolve() == (tmp_path / "models").resolve()
    assert Path(os.environ["NILEARN_DATA"]).resolve() == (tmp_path / "data" / "nilearn").resolve()


def test_configure_env_raises_if_huggingface_hub_already_imported(clean_env, tmp_path, sample_cfg):
    import types

    clean_env.setitem(sys.modules, "huggingface_hub", types.ModuleType("huggingface_hub"))
    paths = settings.resolve_paths(sample_cfg, tmp_path)
    try:
        settings.configure_env(sample_cfg, paths)
    except Exception:
        return
    pytest.fail("configure_env should raise when huggingface_hub is already imported")


def test_max_duration_defaults_to_120_without_config():
    assert settings.max_duration(None) == 120


def test_max_duration_defaults_to_120_when_key_missing():
    assert settings.max_duration({}) == 120


def test_max_duration_reads_config_value():
    assert settings.max_duration({"max_video_duration_seconds": 45}) == 45


def test_no_module_level_max_duration_constant():
    assert not hasattr(settings, "MAX_DURATION")
