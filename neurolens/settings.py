"""Config and paths. Nothing here runs at import time: call the functions when needed."""

import copy
import json
import os
import sys
from pathlib import Path


def server_address(cfg):
    """(host, port) the web app binds to: config `server.host` / `server.port`, by default
    127.0.0.1:5003 (this machine only; the development identity requires it, M3a §2)."""
    server = (cfg or {}).get("server") or {}
    return server.get("host", "127.0.0.1"), server.get("port", 5003)


class UnsafeConfigError(Exception):
    """A setting combination that must never run, e.g. the development identity on a public
    address (M3a §2)."""


def get_root():
    """Project root: NEUROLENS_ROOT if set, else the repo root (two folders above this file).

    Anchored to the file, NOT the working directory, so `python /path/to/app.py` from
    elsewhere still finds the config and does not scatter a ~20 GB download around.
    """
    env = os.environ.get("NEUROLENS_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent


def load_config(root=None):
    root = Path(root) if root is not None else get_root()
    config_path = root / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(
            f"{config_path} not found. It is committed in the repository: "
            "restore it with `git checkout config.json`."
        )
    with open(config_path) as f:
        return json.load(f)


def load_dotenv(root=None):
    """Read `<root>/.env` (KEY=VALUE lines) into os.environ. A missing file is not an error.

    A variable that is already set is left alone, so a real environment variable always wins
    over the file. Blank lines and `#` comments are ignored; one pair of quotes around a value
    is removed; only the first `=` splits, so values may contain `=`.
    """
    path = (Path(root) if root is not None else get_root()) / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def apply_env(cfg):
    """A copy of cfg with person- and deployment-specific values taken from the environment.

    HF_TOKEN -> hf_token; NEUROLENS_AWS_REGION / NEUROLENS_S3_BUCKET / NEUROLENS_SQS_QUEUE_URL /
    NEUROLENS_WORKER_GROUP -> aws.region / aws.s3_bucket / aws.sqs_queue_url / aws.worker_group
    (the last only on AWS workers); NEUROLENS_DB_CLUSTER_ARN / _SECRET_ARN / _NAME ->
    aws.db_cluster_arn / aws.db_secret_arn / aws.db_name; NEUROLENS_DB_DSN -> db.dsn (a local
    PostgreSQL DSN holds a password, so it never goes in config.json). Only variables that are
    set and non-empty change anything. The input is never modified.
    """
    out = copy.deepcopy(cfg)
    token = os.environ.get("HF_TOKEN")
    if token:
        out["hf_token"] = token
    for var, section, key in (
        ("NEUROLENS_AWS_REGION", "aws", "region"),
        ("NEUROLENS_S3_BUCKET", "aws", "s3_bucket"),
        ("NEUROLENS_SQS_QUEUE_URL", "aws", "sqs_queue_url"),
        ("NEUROLENS_WORKER_GROUP", "aws", "worker_group"),
        ("NEUROLENS_DB_CLUSTER_ARN", "aws", "db_cluster_arn"),
        ("NEUROLENS_DB_SECRET_ARN", "aws", "db_secret_arn"),
        ("NEUROLENS_DB_NAME", "aws", "db_name"),
        ("NEUROLENS_DB_DSN", "db", "dsn"),
    ):
        value = os.environ.get(var)
        if value:
            out.setdefault(section, {})[key] = value
    return out


def load_settings(root=None):
    """What entry points use: `.env` into the environment, then config.json plus the overlay."""
    load_dotenv(root)
    return apply_env(load_config(root))


def resolve_paths(cfg, root):
    """Absolute models/data/output folders from the config. Creates nothing."""
    root = Path(root)
    return {name: (root / cfg["paths"][name]).resolve() for name in ("models", "data", "output")}


def ensure_dirs(paths):
    for path in paths.values():
        Path(path).mkdir(parents=True, exist_ok=True)


def configure_env(cfg, paths):
    """Point HuggingFace/torch/nilearn caches at our folders.

    huggingface_hub freezes its cache paths into module-level constants the moment it is
    imported (see huggingface_hub/constants.py), so this must run BEFORE anything imports
    huggingface_hub or transformers, or the weights silently land in ~/.cache/huggingface.
    """
    if "huggingface_hub" in sys.modules:
        raise RuntimeError(
            "huggingface_hub was imported before the cache env vars were set: "
            "model weights would go to ~/.cache/huggingface. Move that import below."
        )

    models = Path(paths["models"])
    data = Path(paths["data"])

    # The token is optional here: it normally arrives through the environment (.env), and fake
    # mode needs none.
    if cfg.get("hf_token"):
        os.environ["HF_TOKEN"] = cfg["hf_token"]

    # HF_HOME is the root the other HF caches derive from; the rest are set explicitly
    # so they survive anyone overriding HF_HOME downstream.
    os.environ["HF_HOME"] = str(models)
    os.environ["HF_HUB_CACHE"] = str(models / "hub")
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(models / "hub")  # legacy alias
    os.environ["HF_ASSETS_CACHE"] = str(models / "assets")
    os.environ["HF_DATASETS_CACHE"] = str(models / "datasets")
    # hf_xet is installed, so downloads stream through its dedup chunk cache.
    # Left unset it defaults under HF_HOME, but it is large enough to be worth pinning.
    os.environ["HF_XET_CACHE"] = str(models / "xet")
    # torch.hub / torchvision weights would otherwise go to ~/.cache/torch.
    os.environ["TORCH_HOME"] = str(models / "torch")

    os.environ["NILEARN_DATA"] = str(data / "nilearn")
    timeout = str(cfg.get("hf_download_timeout", 300))
    os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = timeout
    os.environ["HF_HUB_HTTP_TIMEOUT"] = timeout


# The max duration reflects the target use case (short-form pre-roll / social ad
# creative). The GPU was chosen for it: memory and job time grow with video length
# and were measured only up to 120 s. A 120 s 4K video takes about 40-47 minutes,
# longer than the SQS visibility timeout (1800 s), so until M2b's heartbeat the
# worker group stays at max 1 (M2a §4h); the idle alarm (90 min, M2a §4f) must stay
# longer than the slowest job. This is a hard
# product constraint, independent of billing — it must be enforced
# regardless of credit balance (see M3a §4a `verify`, which must check this
# BEFORE any credit-adjustment logic, not instead of it).
def max_duration(cfg):
    """Longest accepted video in seconds (120 when unset or when there is no config)."""
    if cfg is None:
        return 120
    return cfg.get("max_video_duration_seconds", 120)
