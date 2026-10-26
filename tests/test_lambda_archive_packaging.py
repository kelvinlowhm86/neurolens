"""Lambda archive packaging (infra/terraform/lambdas.tf).

The zip is built from `local.lambda_files`, not from the package on disk. Import the modules from
a scratch copy of exactly those files, with the repository absent from `sys.path`, so a module the
archive forgot fails here instead of in AWS.
"""

import json
import os
import re
import subprocess
import sys
import sysconfig
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAMBDAS_TF = REPO_ROOT / "infra" / "terraform" / "lambdas.tf"

IMPORT_CODE = """
import importlib, json, sys
from pathlib import Path

scratch = Path(sys.argv[1]).resolve()
repo = Path(sys.argv[2]).resolve()
site_packages = Path(sys.argv[3]).resolve()
modules = json.loads(sys.argv[4])

sys.path[:] = [str(scratch)] + [
    p for p in sys.path
    if p and not Path(p).resolve().is_relative_to(repo) and Path(p).resolve() != scratch
]
sys.path.append(str(site_packages))

loaded = {}
for name in modules:
    module = importlib.import_module(name)
    loaded[name] = str(Path(module.__file__).resolve())
print(json.dumps(loaded))
"""


def declared_files():
    match = re.search(r"lambda_files\s*=\s*\[(.*?)\]", LAMBDAS_TF.read_text(), re.DOTALL)
    assert match, "local.lambda_files not found in infra/terraform/lambdas.tf"
    files = re.findall(r'"([^"]+)"', match.group(1))
    assert files, "local.lambda_files is empty"
    return files


def archive_modules(files):
    modules = []
    for name in files:
        if not name.endswith(".py"):
            continue
        module = name[:-3].replace("/", ".")
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        modules.append(module)
    assert modules, "the archive declares no Python modules"
    return modules


def test_the_declared_archive_files_import_without_the_repository_on_the_path(tmp_path):
    files = declared_files()
    for name in files:
        source = REPO_ROOT / name
        assert source.is_file(), f"{name} is declared in local.lambda_files but missing"
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())

    modules = archive_modules(files)
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")
    }
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            IMPORT_CODE,
            str(tmp_path),
            str(REPO_ROOT),
            sysconfig.get_paths()["purelib"],
            json.dumps(modules),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    loaded = json.loads(proc.stdout.strip().splitlines()[-1])
    assert set(loaded) == set(modules)
    for name, origin in loaded.items():
        assert Path(origin).is_relative_to(tmp_path.resolve()), f"{name} came from {origin}"
