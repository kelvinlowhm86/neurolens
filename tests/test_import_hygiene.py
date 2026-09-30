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


# ---- Added in M1 (docs/M1_spec.md section 6a): the new light modules stay light.
M1_MODULES = ["neurolens.storage", "neurolens.pricing", "neurolens.worker"]


@pytest.mark.parametrize("module", M1_MODULES)
def test_m1_modules_import_without_heavy_modules(module):
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


@pytest.mark.parametrize("module", ["neurolens.storage", "neurolens.pricing"])
def test_storage_and_pricing_import_without_neurolens_inference(module):
    code = (
        "import importlib, sys; "
        f"importlib.import_module({module!r}); "
        "print('neurolens.inference' in sys.modules)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "False"


# ---- Added in M1 cleanup (docs/M1_spec.md section 7): the web tier never loads the model code.
CREATE_APP_CODE = """
import json, sys, tempfile
from pathlib import Path
from neurolens.web.app import create_app
data = Path(tempfile.mkdtemp())
cfg = {cfg!r}
create_app(data_dir=data, cfg=cfg)
print(json.dumps([m for m in ("neurolens.inference", "torch") if m in sys.modules]))
"""

CFG_WITHOUT_AWS = {
    "paths": {"models": "m", "data": "d", "output": "o"},
    "max_video_duration_seconds": 120,
    "max_upload_bytes": 300000000,
}


@pytest.mark.parametrize("cfg", [None, CFG_WITHOUT_AWS], ids=["cfg_none", "cfg_without_aws"])
def test_create_app_does_not_load_inference_or_torch(cfg):
    proc = subprocess.run(
        [sys.executable, "-c", CREATE_APP_CODE.format(cfg=cfg)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == []
