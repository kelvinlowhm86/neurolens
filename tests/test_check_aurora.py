"""infra/check_aurora.py stays importable and covers the current jobs columns (M3e §10).

It is an operator tool, not run against a database here: this pins its imports and argument
parsing, and that the type checks include migration 005's retention columns.
"""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "infra" / "check_aurora.py"


def load():
    spec = importlib.util.spec_from_file_location("check_aurora", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_check_aurora_imports_and_its_type_checks_cover_migration_005():
    module = load()
    assert "retention_added_at" in module.EXPECTED_TYPES
    assert "dataset_removed_at" in module.EXPECTED_TYPES
    assert "research_consent" in module.EXPECTED_TYPES
    assert "deleted_at" in module.EXPECTED_TYPES


def test_check_aurora_argparse_works_without_touching_a_database(monkeypatch):
    module = load()
    monkeypatch.setattr(
        module.settings, "load_settings", lambda: pytest.fail("must not load settings for --help")
    )
    with pytest.raises(SystemExit) as err:
        module.main(["--help"])
    assert err.value.code == 0
