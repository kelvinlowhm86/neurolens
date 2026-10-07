"""M3a import hygiene. Written first from docs/M3a_spec.md §3d, §7 and §10: the database layer,
billing and the Lambdas' modules import without numpy (the Lambda zip has none), psycopg (only
the PostgreSQL backend needs it, imported lazily inside the class) or neurolens.inference."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = ["numpy", "psycopg", "neurolens.inference", "torch"]
MODULES = [
    "neurolens.db",
    "neurolens.billing",
    "neurolens.pricing",
    "neurolens.storage",
    "neurolens.settings",
    "neurolens.lambdas.dlq_handler",
    "neurolens.lambdas.reaper",
]


def loaded_after_import(code):
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("module", MODULES)
def test_module_imports_without_numpy_psycopg_or_inference(module):
    code = (
        "import importlib, json, sys; "
        f"importlib.import_module({module!r}); "
        f"print(json.dumps([m for m in {FORBIDDEN!r} if m in sys.modules]))"
    )
    assert loaded_after_import(code) == []


def test_all_lambda_modules_together_import_without_them():
    code = (
        "import json, sys; "
        + "".join(f"import {m}; " for m in MODULES)
        + f"print(json.dumps([m for m in {FORBIDDEN!r} if m in sys.modules]))"
    )
    assert loaded_after_import(code) == []
