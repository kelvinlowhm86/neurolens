"""Tests for the split between shared settings (config.json) and secrets (.env).

Written from docs/M1_spec.md section 2. conftest.py replaces `load_dotenv` with a no-op for
every test; the tests here that need the real one put it back with `real_load_dotenv`.
"""

import copy
import json
import re
import sys
from pathlib import Path

import pytest
from neurolens import inference, settings, worker
from neurolens.settings import load_dotenv as real_load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

ENV_TO_KEY = {
    "HF_TOKEN": ("hf_token",),
    "NEUROLENS_AWS_REGION": ("aws", "region"),
    "NEUROLENS_S3_BUCKET": ("aws", "s3_bucket"),
    "NEUROLENS_SQS_QUEUE_URL": ("aws", "sqs_queue_url"),
}


@pytest.fixture
def real_dotenv(monkeypatch):
    monkeypatch.setattr(settings, "load_dotenv", real_load_dotenv)


@pytest.fixture
def var(monkeypatch):
    """Register a variable name with monkeypatch (and unset it) so the real env is restored."""

    def reg(name):
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)
        return name

    return reg


def write_env(root, text):
    (root / ".env").write_text(text)


# ------------------------------------------------------------------ load_dotenv


def test_load_dotenv_sets_key_value_lines(tmp_path, var, monkeypatch):
    import os

    a, b = var("NL_TEST_A"), var("NL_TEST_B")
    write_env(tmp_path, "NL_TEST_A=one\nNL_TEST_B=two\n")
    assert real_load_dotenv(tmp_path) is None
    assert os.environ[a] == "one"
    assert os.environ[b] == "two"


def test_load_dotenv_ignores_blank_lines_and_comments(tmp_path, var):
    import os

    a = var("NL_TEST_A")
    before = dict(os.environ)
    write_env(tmp_path, "\n# a comment\n   \nNL_TEST_A=one\n# NL_TEST_C=hidden\n\n")
    real_load_dotenv(tmp_path)
    assert os.environ[a] == "one"
    assert "NL_TEST_C" not in os.environ
    assert set(os.environ) - set(before) == {"NL_TEST_A"}


@pytest.mark.parametrize("raw, expected", [('"double q"', "double q"), ("'single q'", "single q")])
def test_load_dotenv_strips_one_pair_of_quotes(tmp_path, var, raw, expected):
    import os

    a = var("NL_TEST_A")
    write_env(tmp_path, f"NL_TEST_A={raw}\n")
    real_load_dotenv(tmp_path)
    assert os.environ[a] == expected


def test_load_dotenv_strips_only_one_pair_of_quotes(tmp_path, var):
    import os

    a = var("NL_TEST_A")
    write_env(tmp_path, "NL_TEST_A=\"'inner'\"\n")
    real_load_dotenv(tmp_path)
    assert os.environ[a] == "'inner'"


def test_load_dotenv_splits_on_the_first_equals_only(tmp_path, var):
    import os

    a = var("NL_TEST_A")
    url = "https://sqs.us-east-1.amazonaws.com/123/q?a=b&c=d=="
    write_env(tmp_path, f"NL_TEST_A={url}\n")
    real_load_dotenv(tmp_path)
    assert os.environ[a] == url


def test_load_dotenv_missing_file_is_not_an_error(tmp_path):
    import os

    before = dict(os.environ)
    assert real_load_dotenv(tmp_path) is None
    assert dict(os.environ) == before


def test_load_dotenv_does_not_overwrite_a_real_environment_variable(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("NL_TEST_A", "from-environment")
    write_env(tmp_path, "NL_TEST_A=from-file\n")
    real_load_dotenv(tmp_path)
    assert os.environ["NL_TEST_A"] == "from-environment"


def test_load_dotenv_uses_the_root_folder_not_the_working_directory(tmp_path, var, monkeypatch):
    import os

    a = var("NL_TEST_A")
    other = tmp_path / "other"
    other.mkdir()
    write_env(other, "NL_TEST_A=wrong\n")
    root = tmp_path / "root"
    root.mkdir()
    write_env(root, "NL_TEST_A=right\n")
    monkeypatch.chdir(other)
    real_load_dotenv(root)
    assert os.environ[a] == "right"


# ------------------------------------------------------------------ apply_env


@pytest.mark.parametrize("name, path", list(ENV_TO_KEY.items()), ids=list(ENV_TO_KEY))
def test_apply_env_maps_each_variable(monkeypatch, name, path):
    monkeypatch.setenv(name, "the-value")
    out = settings.apply_env({})
    node = out
    for part in path:
        node = node[part]
    assert node == "the-value"


def test_apply_env_creates_the_aws_dict_when_absent(monkeypatch):
    monkeypatch.setenv("NEUROLENS_S3_BUCKET", "b")
    out = settings.apply_env({"paths": {"output": "o"}})
    assert out["aws"] == {"s3_bucket": "b"}


def test_apply_env_leaves_other_keys_untouched(monkeypatch):
    monkeypatch.setenv("NEUROLENS_S3_BUCKET", "b")
    cfg = {
        "paths": {"output": "o"},
        "max_upload_bytes": 5,
        "aws": {"region": "eu-west-1", "extra": "keep"},
    }
    out = settings.apply_env(cfg)
    assert out["paths"] == {"output": "o"}
    assert out["max_upload_bytes"] == 5
    assert out["aws"] == {"region": "eu-west-1", "extra": "keep", "s3_bucket": "b"}


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
def test_apply_env_ignores_unset_or_empty_variables(monkeypatch, value):
    for name in ENV_TO_KEY:
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    cfg = {"paths": {"output": "o"}}
    out = settings.apply_env(cfg)
    assert out == cfg
    assert "hf_token" not in out
    assert "aws" not in out


def test_apply_env_empty_variable_does_not_blank_a_file_value(monkeypatch):
    monkeypatch.setenv("NEUROLENS_AWS_REGION", "")
    out = settings.apply_env({"aws": {"region": "us-east-1"}})
    assert out["aws"]["region"] == "us-east-1"


def test_apply_env_never_mutates_its_input(monkeypatch):
    for name in ENV_TO_KEY:
        monkeypatch.setenv(name, "x")
    cfg = {"aws": {"region": "us-east-1"}, "paths": {"output": "o"}}
    snapshot = copy.deepcopy(cfg)
    out = settings.apply_env(cfg)
    assert cfg == snapshot
    assert out is not cfg
    assert out["aws"] is not cfg["aws"]
    out["aws"]["region"] = "changed"
    assert cfg == snapshot


def test_apply_env_with_nothing_set_returns_an_equal_copy(monkeypatch):
    cfg = {"aws": {"region": "us-east-1"}}
    out = settings.apply_env(cfg)
    assert out == cfg
    assert out is not cfg


def test_apply_env_value_overrides_the_file_value(monkeypatch):
    monkeypatch.setenv("NEUROLENS_AWS_REGION", "ap-southeast-1")
    monkeypatch.setenv("HF_TOKEN", "from-env")
    out = settings.apply_env({"hf_token": "from-file", "aws": {"region": "us-east-1"}})
    assert out["aws"]["region"] == "ap-southeast-1"
    assert out["hf_token"] == "from-env"


# ------------------------------------------------------------------ load_settings

FILE_CFG = {
    "paths": {"models": "./models", "data": "./data", "output": "./output"},
    "model": {"repo_id": "facebook/tribev2"},
    "aws": {"region": "us-east-1"},
    "hf_download_timeout": 300,
    "max_video_duration_seconds": 120,
    "max_upload_bytes": 300000000,
}


@pytest.fixture
def root(tmp_path, real_dotenv):
    (tmp_path / "config.json").write_text(json.dumps(FILE_CFG))
    return tmp_path


def test_load_settings_overlays_dotenv_on_config_json(root):
    write_env(
        root,
        "HF_TOKEN=hf_fake\nNEUROLENS_S3_BUCKET=my-bucket\n"
        "NEUROLENS_SQS_QUEUE_URL=https://q.example/1\nNEUROLENS_AWS_REGION=eu-west-1\n",
    )
    cfg = settings.load_settings(root)
    assert cfg["hf_token"] == "hf_fake"
    assert cfg["aws"] == {
        "region": "eu-west-1",
        "s3_bucket": "my-bucket",
        "sqs_queue_url": "https://q.example/1",
    }
    assert cfg["paths"] == FILE_CFG["paths"]
    assert cfg["max_upload_bytes"] == FILE_CFG["max_upload_bytes"]


def test_load_settings_without_dotenv_or_env_equals_load_config(root):
    assert not (root / ".env").exists()
    assert settings.load_settings(root) == settings.load_config(root)


def test_load_settings_real_environment_beats_dotenv(root, monkeypatch):
    write_env(root, "NEUROLENS_S3_BUCKET=from-file\nNEUROLENS_SQS_QUEUE_URL=u-file\n")
    monkeypatch.setenv("NEUROLENS_S3_BUCKET", "from-environment")
    cfg = settings.load_settings(root)
    assert cfg["aws"]["s3_bucket"] == "from-environment"
    assert cfg["aws"]["sqs_queue_url"] == "u-file"


def test_load_settings_defaults_root_to_get_root(root, monkeypatch):
    monkeypatch.setenv("NEUROLENS_ROOT", str(root))
    write_env(root, "NEUROLENS_S3_BUCKET=b\n")
    assert settings.load_settings()["aws"]["s3_bucket"] == "b"


def test_load_settings_missing_config_json_names_the_file(tmp_path, real_dotenv):
    with pytest.raises(FileNotFoundError, match=r"config\.json"):
        settings.load_settings(tmp_path)


# ------------------------------------------------------------------ configure_env and hf_token


@pytest.fixture
def hf_clean(monkeypatch):
    """configure_env refuses to run if huggingface_hub is imported; restore the env after."""
    for name in (
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
    ):
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)
    monkeypatch.delitem(sys.modules, "huggingface_hub", raising=False)
    return monkeypatch


def test_configure_env_without_hf_token_leaves_existing_token_alone(hf_clean, tmp_path):
    hf_clean.setenv("HF_TOKEN", "hf_from_environment")
    cfg = copy.deepcopy(FILE_CFG)
    paths = settings.resolve_paths(cfg, tmp_path)
    settings.configure_env(cfg, paths)
    import os

    assert os.environ["HF_TOKEN"] == "hf_from_environment"
    assert os.environ["HF_HOME"]


def test_configure_env_without_hf_token_and_no_env_token_does_not_raise(hf_clean, tmp_path):
    import os

    cfg = copy.deepcopy(FILE_CFG)
    paths = settings.resolve_paths(cfg, tmp_path)
    settings.configure_env(cfg, paths)
    assert "HF_TOKEN" not in os.environ


def test_configure_env_with_hf_token_still_sets_it(hf_clean, tmp_path):
    import os

    hf_clean.setenv("HF_TOKEN", "old")
    cfg = {**copy.deepcopy(FILE_CFG), "hf_token": "hf_from_cfg"}
    paths = settings.resolve_paths(cfg, tmp_path)
    settings.configure_env(cfg, paths)
    assert os.environ["HF_TOKEN"] == "hf_from_cfg"


# ------------------------------------------------------------------ entry points use load_settings


def test_worker_run_reads_its_config_through_load_settings(make_cfg, monkeypatch):
    cfg = make_cfg()
    del cfg["aws"]["s3_bucket"]
    loaded = []

    def load_model(*a, **kw):
        loaded.append(True)
        raise AssertionError("model must not load with a bad config")

    def boom(*a, **kw):
        raise AssertionError("worker must not read the file-only load_config")

    monkeypatch.setattr(settings, "load_settings", lambda *a, **kw: cfg)
    monkeypatch.setattr(settings, "load_config", boom)
    monkeypatch.setattr(inference, "load_model", load_model)
    if hasattr(worker, "load_model"):
        monkeypatch.setattr(worker, "load_model", load_model)

    with pytest.raises(Exception, match="s3_bucket"):
        worker.run()
    assert loaded == []


def test_load_model_reads_its_config_through_load_settings(monkeypatch):
    class Sentinel(Exception):
        pass

    def fake_load_settings(*a, **kw):
        raise Sentinel

    monkeypatch.setenv("FAKE_INFERENCE", "1")
    monkeypatch.setattr(settings, "load_settings", fake_load_settings)
    with pytest.raises(Sentinel):
        inference.load_model(None)


# ------------------------------------------------------------------ secret-leak guard


def _walk(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)
    else:
        yield node


@pytest.fixture(scope="module")
def committed_cfg():
    return json.loads((REPO_ROOT / "config.json").read_text())


def test_committed_config_has_no_hf_token(committed_cfg):
    assert "hf_token" not in committed_cfg


def test_committed_config_aws_holds_only_the_region(committed_cfg):
    assert set(committed_cfg.get("aws", {})) <= {"region"}


def test_committed_config_has_no_secret_looking_values(committed_cfg):
    for item in _walk(committed_cfg):
        if not isinstance(item, str):
            continue
        assert not item.startswith("hf_"), item
        assert ".amazonaws.com" not in item, item
        assert not re.search(r"(?<!\d)\d{12}(?!\d)", item), item


def test_committed_config_keeps_the_shared_shape(committed_cfg):
    assert set(committed_cfg["paths"]) >= {"models", "data", "output"}
    assert "max_upload_bytes" in committed_cfg
    assert "max_video_duration_seconds" in committed_cfg


def test_env_example_is_committed_with_placeholders():
    path = REPO_ROOT / ".env.example"
    assert path.is_file()
    text = path.read_text()
    for name in ("HF_TOKEN", "NEUROLENS_S3_BUCKET", "NEUROLENS_SQS_QUEUE_URL"):
        assert name in text
    for line in text.splitlines():
        assert not re.search(r"hf_[A-Za-z0-9]{20,}", line), "looks like a real token"
        assert not re.search(r"(?<!\d)\d{12}(?!\d)", line), "looks like an account number"


def test_env_example_can_be_parsed_by_load_dotenv(tmp_path, real_dotenv, monkeypatch):
    """The example file must be valid input (placeholders only) for the real parser."""
    import os

    for name in ("HF_TOKEN", "NEUROLENS_S3_BUCKET", "NEUROLENS_SQS_QUEUE_URL"):
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)
    (tmp_path / ".env").write_text((REPO_ROOT / ".env.example").read_text())
    real_load_dotenv(tmp_path)
    assert "HF_TOKEN" in os.environ
