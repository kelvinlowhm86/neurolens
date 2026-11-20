#!/usr/bin/env python3
"""Fill s3://<bucket>/model-data/ from scratch for the SageMaker worker.

SageMaker copies that prefix into /opt/ml/model at instance start, and the container runs with
HF_HUB_OFFLINE=1. The prefix therefore mirrors the repo's paths: models/hub/ holds the HuggingFace
cache, models/torch/ the torchaudio alignment weights, data/hcp_mmp/ the two Glasser maps the
worker verifies at start, and data/nilearn/ the Destrieux atlas the brain mesh is built from.

The main model repository comes from config.json and the map names and SHA-256s from response.py,
so those are not repeated here. Everything is downloaded into a temporary folder on this laptop,
checked, uploaded, checked again against S3, and the temporary folder is removed.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from neurolens import settings
from neurolens.inference import MAP_DOWNLOAD_TIMEOUT_SECONDS
from neurolens.response import HCP_MMP_FILES, verify_sha256

ROOT = Path(__file__).resolve().parent.parent
TF_DIR = ROOT / "infra" / "terraform"
PREFIX = "model-data/"

# Each repository is downloaded with the file patterns the offline worker reads, never whole:
# several also publish large files it never opens (the video encoder's original/model.pth alone
# is 16 GB), which would bloat the download, S3 and every instance start.
# The TRIBE v2 checkpoint and its config (the repository itself comes from config.json).
MODEL_FILES = ["*.ckpt", "*.yaml"]

# TRIBE v2's three frozen encoders, named in the model's config.yaml on HuggingFace. They are
# pinned with the model, not deployment settings. LLaMA is gated: the token needs an accepted
# licence. Each needs only its configs, tokenizer and safetensors weights.
ENCODER_REPOS = {
    "meta-llama/Llama-3.2-3B": ["*.json", "*.safetensors"],
    "facebook/w2v-bert-2.0": ["*.json", "*.safetensors"],
    "facebook/vjepa2-vitg-fpc64-256": ["*.json", "*.safetensors"],
}

# WhisperX transcribes speech with faster-whisper's large-v3 model (the worker image installs
# WhisperX; the model itself is not in the image, so it must be in this offline cache).
WHISPER_REPO = {"Systran/faster-whisper-large-v3": ["*.json", "model.bin"]}

# WhisperX aligns words with torchaudio's WAV2VEC2_ASR_LARGE_LV60K_960H pipeline. torch.hub keeps
# downloaded checkpoints under $TORCH_HOME/hub/checkpoints; this is the URL and file from that
# pipeline (torchaudio.pipelines), with the published size used as the integrity check.
ALIGNMENT_WEIGHTS = (
    "wav2vec2_fairseq_large_lv60k_asr_ls960.pth",
    "https://download.pytorch.org/torchaudio/models/wav2vec2_fairseq_large_lv60k_asr_ls960.pth",
    1_262_046_195,
)

# nilearn.datasets.fetch_atlas_surf_destrieux writes these under NILEARN_DATA/destrieux_surface,
# named after the left/right hemispheres, and adds NILEARN_README at NILEARN_DATA itself. The
# worker's mesh maker reads the annot files; the README is what nilearn would have written.
DESTRIEUX_FILES = {
    "left.aparc.a2009s.annot": (
        "https://www.nitrc.org/frs/download.php/9343/lh.aparc.a2009s.annot",
        85_260,
    ),
    "right.aparc.a2009s.annot": (
        "https://www.nitrc.org/frs/download.php/9342/rh.aparc.a2009s.annot",
        85_260,
    ),
}
NILEARN_README = """# Nilearn data folder

This directory is used by Nilearn to store datasets
and atlases downloaded from the internet.
It can be safely deleted.
If you delete it, previously downloaded data will be downloaded again."""

DOWNLOAD_TIMEOUT_SECONDS = 3600  # the alignment weights are 1.26 GB


def terraform_output(name):
    result = subprocess.run(
        ["terraform", f"-chdir={TF_DIR}", "output", "-raw", name],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def download_huggingface(repo_id, cache_dir, token, allow_patterns):
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=repo_id,
        cache_dir=str(cache_dir),
        token=token,
        allow_patterns=allow_patterns,
    )


def download_repos(cfg, token, cache_dir):
    repositories = {
        cfg["model"]["repo_id"]: MODEL_FILES,
        **ENCODER_REPOS,
        **WHISPER_REPO,
    }
    for repo_id, allow_patterns in repositories.items():
        print(f"Downloading {repo_id}...")
        download_huggingface(repo_id, cache_dir, token, allow_patterns)


def download_file(url, target, expected_size):
    """Download one file atomically and check its size, so a partial file is never uploaded."""
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {target.name}...")
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=f"{target.name}.", suffix=".tmp")
    os.close(fd)
    temporary = Path(temporary)
    replaced = False
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            with temporary.open("wb") as handle:
                shutil.copyfileobj(response, handle)
        actual = temporary.stat().st_size
        if actual != expected_size:
            raise RuntimeError(f"{target.name} is {actual} bytes, expected {expected_size}.")
        temporary.replace(target)
        replaced = True
    finally:
        if not replaced:
            temporary.unlink(missing_ok=True)


def download_torch_weights(models_dir):
    filename, url, expected_size = ALIGNMENT_WEIGHTS
    download_file(url, models_dir / "torch" / "hub" / "checkpoints" / filename, expected_size)


def download_maps(data_dir):
    maps_dir = data_dir / "hcp_mmp"
    maps_dir.mkdir(parents=True, exist_ok=True)
    for filename, url, expected in HCP_MMP_FILES.values():
        target = maps_dir / filename
        print(f"Downloading {filename}...")
        with urllib.request.urlopen(url, timeout=MAP_DOWNLOAD_TIMEOUT_SECONDS) as response:
            target.write_bytes(response.read())
        verify_sha256(target, expected)


def download_nilearn(data_dir):
    nilearn_dir = data_dir / "nilearn"
    nilearn_dir.mkdir(parents=True, exist_ok=True)
    (nilearn_dir / "README.md").write_text(NILEARN_README)
    for filename, (url, expected_size) in DESTRIEUX_FILES.items():
        download_file(url, nilearn_dir / "destrieux_surface" / filename, expected_size)


def prepare_model_data(cfg, token, hub_cache, models_dir, data_dir):
    download_repos(cfg, token, hub_cache)
    download_torch_weights(models_dir)
    download_maps(data_dir)
    download_nilearn(data_dir)


def upload_plan(hub_cache, models_dir, data_dir):
    """S3 key -> local file for every file the offline worker reads, uploaded in place (no
    second copy on disk). Links are followed and HuggingFace's duplicated blobs/ are skipped."""
    trees = {
        "models/hub": hub_cache,
        "models/torch": models_dir / "torch",
        "data/hcp_mmp": data_dir / "hcp_mmp",
        "data/nilearn": data_dir / "nilearn",
    }
    plan = {}
    for prefix, src in trees.items():
        for path in sorted(src.rglob("*")):
            relative = path.relative_to(src)
            if "blobs" not in relative.parts and path.is_file():
                plan[f"{PREFIX}{prefix}/{relative.as_posix()}"] = path
    return plan


def totals(files):
    files = list(files)
    return len(files), sum(path.stat().st_size for path in files)


def remote_totals(s3, bucket):
    count = size = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=PREFIX):
        for item in page.get("Contents", []):
            count += 1
            size += item["Size"]
    return count, size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", help="S3 bucket (default: Terraform's bucket_name output)")
    args = parser.parse_args()

    cfg = settings.load_settings(ROOT)
    token = cfg.get("hf_token")
    if not token:
        raise SystemExit(
            "HF_TOKEN is missing: copy .env.example to .env and fill in the Hugging Face token."
        )
    bucket = args.bucket or terraform_output("bucket_name")
    region = terraform_output("region")

    temp = Path(tempfile.mkdtemp(prefix="neurolens-model-data-"))
    try:
        paths = {"models": temp / "models", "data": temp / "data"}
        for path in paths.values():
            path.mkdir(parents=True)
        settings.configure_env(cfg, paths)

        hub_cache = Path(os.environ["HF_HUB_CACHE"])
        prepare_model_data(cfg, token, hub_cache, paths["models"], paths["data"])
        plan = upload_plan(hub_cache, paths["models"], paths["data"])
        local = totals(plan.values())
        if local[0] == 0 or local[1] == 0:
            raise SystemExit("The downloaded model files are empty; nothing to upload.")

        import boto3

        s3 = boto3.client("s3", region_name=region)
        uploaded = []
        try:
            print(f"Uploading {local[0]} files ({local[1]} bytes) to s3://{bucket}/{PREFIX}...")
            for index, (key, path) in enumerate(plan.items(), 1):
                s3.upload_file(str(path), bucket, key)
                uploaded.append(key)
                if index % 100 == 0 or index == len(plan):
                    print(f"  {index}/{len(plan)}")
            remote = remote_totals(s3, bucket)
            if remote != local:
                raise SystemExit(
                    f"Upload check failed: local {local[0]} objects/{local[1]} bytes, "
                    f"S3 {remote[0]} objects/{remote[1]} bytes."
                )
        except BaseException:
            print("Removing the partial upload...", file=sys.stderr)
            for key in uploaded:
                s3.delete_object(Bucket=bucket, Key=key)
            raise

        print(
            f"model-data/ holds {remote[0]} objects, {remote[1]} bytes "
            f"(matches the {local[0]} downloaded files)."
        )
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    main()
