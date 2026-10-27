"""
Fetch every model asset into MODELS_DIR so the worker can run with HF_HUB_OFFLINE=1.

Used at AMI bake time (and once locally). Needs HF_TOKEN with access to the gated LLaMA repo.
TRIBE v2 loads its encoders lazily, so after the explicit downloads we run one prediction on a
sample video *with speech* — that pulls V-JEPA2, Wav2Vec-BERT, WhisperX and LLaMA via their own code.

Acceptance test for a bake:  HF_HUB_OFFLINE=1 python -m neurolens_worker warmup --sample <video>
"""

import logging
from pathlib import Path

from neurolens_common.config import Settings

logger = logging.getLogger("neurolens.worker.weights")

LLAMA_REPO = "meta-llama/Llama-3.2-3B"


def download_all(cfg: Settings, sample: Path) -> None:
    from huggingface_hub import snapshot_download

    if not sample.is_file():
        raise FileNotFoundError(f"Sample video not found: {sample} (use an ad with spoken dialogue)")

    logger.info("Downloading %s", cfg.tribe_repo)
    snapshot_download(cfg.tribe_repo)
    logger.info("Downloading %s (gated — needs accepted licence)", LLAMA_REPO)
    snapshot_download(LLAMA_REPO, allow_patterns=["*.json", "*.safetensors", "tokenizer*"])

    from .inference.tribe import TribeBackend

    backend = TribeBackend(cfg)
    backend.load()                       # also fetches the Destrieux atlas via nilearn
    logger.info("Running a full prediction on %s to pull the lazily-loaded encoders", sample)
    backend.warmup(sample)
    logger.info("All weights cached under %s", cfg.models_dir)
