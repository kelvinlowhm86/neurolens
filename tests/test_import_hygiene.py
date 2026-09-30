"""Importing the light modules must not pull in heavy libraries. From docs/M0_spec.md."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HEAVY = ["torch", "huggingface_hub", "nilearn"]
MODULES = ["neurolens", "neurolens.engagement", "neurolens.settings", "neurolens.web.app"]


@pytest.mark.parametrize("module", MODULES)
def test_import_pulls_in_no_heavy_modules(module):
    code = (
        "import importlib, json, sys; "
        f"importlib.import_module({module!r}); "
        f"print(json.dumps([m for m in {HEAVY!r} if m in sys.modules]))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == []


def test_all_four_together_pull_in_no_heavy_modules():
    code = (
        "import json, sys; "
        "import neurolens, neurolens.engagement, neurolens.settings, neurolens.web.app; "
        f"print(json.dumps([m for m in {HEAVY!r} if m in sys.modules]))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == []
