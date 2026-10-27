# syntax=docker/dockerfile:1.7
# NeuroLens GPU worker. Build from the repo root:
#   docker build -f worker/Dockerfile --target gpu  .   # real TRIBE v2 (AWS AMI / GPU hosts)
#   docker build -f worker/Dockerfile --target mock .   # fake inference, no torch (CI, laptops)
# Model weights are NOT in the image: mount them at /models (read-only).

# ── shared base: Python 3.11 (same as .venv-worker) + ffmpeg ──────────────────
FROM python:3.11-slim AS base
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 10001 app && mkdir -p /scratch && chown app /scratch
WORKDIR /app

# ── mock ──────────────────────────────────────────────────────────────────────
FROM base AS mock
RUN pip install numpy==2.2.6 boto3==1.42.73 matplotlib==3.10.8 reportlab==5.1.0 pillow==11.0.0
COPY packages/common /src/common
RUN pip install /src/common
COPY worker/neurolens_worker ./neurolens_worker
ENV INFERENCE_BACKEND=mock SCRATCH_DIR=/scratch REPORT_LLM=mock
USER app
ENTRYPOINT ["python", "-m", "neurolens_worker"]
CMD ["run"]

# ── gpu ───────────────────────────────────────────────────────────────────────
# Layer order is the cold-start strategy: the big dependency layer rarely changes and is pre-pulled
# into the AMI; a release only changes the last few KB (our code), so `docker pull` at boot is quick.
FROM base AS gpu
COPY worker/requirements.lock /tmp/requirements.lock
RUN pip install --no-deps -r /tmp/requirements.lock --extra-index-url https://download.pytorch.org/whl/cu126
COPY packages/common /src/common
RUN pip install --no-deps /src/common
COPY worker/neurolens_worker ./neurolens_worker
ENV HF_HOME=/models HF_HUB_OFFLINE=1 NILEARN_DATA=/models/nilearn TORCH_HOME=/models/torch MODELS_DIR=/models \
    SCRATCH_DIR=/scratch INFERENCE_BACKEND=tribe
USER app
ENTRYPOINT ["python", "-m", "neurolens_worker"]
CMD ["run"]
