#!/usr/bin/env python3
"""Fill s3://<bucket>/model-data/ from scratch for the SageMaker worker.

SageMaker copies that prefix into /opt/ml/model at instance start, and the container runs with
HF_HUB_OFFLINE=1. The prefix therefore mirrors the repo's paths: models/hub/ holds the HuggingFace
cache and data/hcp_mmp/ the two Glasser maps the worker verifies at start.

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

# TRIBE v2's three frozen encoders, named in the model's config.yaml on HuggingFace. They are
# pinned with the model, not deployment settings. LLaMA is gated: the token needs an accepted
# licence. The text encoder only needs its tokenizer and weights, not the original .bin files.
ENCODER_REPOS = {
    "meta-llama/Llama-3.2-3B": ["*.json", "*.safetensors", "tokenizer*"],
    "facebook/w2v-bert-2.0": None,
    "facebook/vjepa2-vitg-fpc64-256": None,
}


def terraform_output(name):
    result = subprocess.run(
        ["terraform", f"-chdir={TF_DIR}", "output", "-raw", name],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def download_repos(cfg, token):
    from huggingface_hub import snapshot_download

    repo_id = cfg["model"]["repo_id"]
    cache_dir = os.environ["HF_HUB_CACHE"]
    print(f"Downloading {repo_id}...")
    snapshot_download(repo_id=repo_id, cache_dir=cache_dir, token=token)
    for encoder, allow_patterns in ENCODER_REPOS.items():
        print(f"Downloading {encoder}...")
        snapshot_download(
            repo_id=encoder,
            cache_dir=cache_dir,
            token=token,
            allow_patterns=allow_patterns,
        )


def download_maps(data_dir):
    maps_dir = data_dir / "hcp_mmp"
    maps_dir.mkdir(parents=True, exist_ok=True)
    for filename, url, expected in HCP_MMP_FILES.values():
        target = maps_dir / filename
        print(f"Downloading {filename}...")
        with urllib.request.urlopen(url, timeout=MAP_DOWNLOAD_TIMEOUT_SECONDS) as response:
            target.write_bytes(response.read())
        verify_sha256(target, expected)


def copy_tree(src, dst):
    """Copy files and folders, following links, without HuggingFace's duplicated blobs/."""
    for path in src.rglob("*"):
        relative = path.relative_to(src)
        if "blobs" in relative.parts:
            continue
        if path.is_dir():
            (dst / relative).mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target = dst / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


def totals(files):
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

        download_repos(cfg, token)
        download_maps(paths["data"])

        stage = temp / "upload"
        copy_tree(Path(os.environ["HF_HUB_CACHE"]), stage / "models" / "hub")
        copy_tree(paths["data"] / "hcp_mmp", stage / "data" / "hcp_mmp")
        files = [path for path in stage.rglob("*") if path.is_file()]
        local = totals(files)
        if local[0] == 0 or local[1] == 0:
            raise SystemExit("The downloaded model files are empty; nothing to upload.")

        import boto3

        s3 = boto3.client("s3", region_name=region)
        uploaded = []
        try:
            print(f"Uploading {local[0]} files ({local[1]} bytes) to s3://{bucket}/{PREFIX}...")
            for index, path in enumerate(files, 1):
                key = PREFIX + path.relative_to(stage).as_posix()
                s3.upload_file(str(path), bucket, key)
                uploaded.append(key)
                if index % 100 == 0 or index == len(files):
                    print(f"  {index}/{len(files)}")
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
