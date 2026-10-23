# NeuroLens SageMaker container (docs/M3e_spec.md §4d).
#
# The base is AWS's PyTorch 2.6.0 GPU inference image for SageMaker (Python 3.12, CUDA 12.4),
# pinned by digest (looked up 23 Oct 2026; infra/terraform/README.md §2b has the command).
ARG BASE_IMAGE=763104351884.dkr.ecr.us-east-1.amazonaws.com/pytorch-inference:2.6.0-gpu-py312-cu124-ubuntu22.04-sagemaker@sha256:f5c4556668fe14fb9b7f97db0dd54cca324736c6ecd9cfa390b08c5c5aab0172
FROM ${BASE_IMAGE}

# Runtime settings (M3e §4d). UV_OFFLINE is off during the build so uv can fill its cache, then on
# so a job never reaches PyPI. HF_HUB_OFFLINE is safe: SageMaker copies the weights in.
ENV NEUROLENS_ROOT=/opt/neurolens \
    NEUROLENS_DEPLOYED=1 \
    HF_HUB_OFFLINE=1 \
    PYTHONUNBUFFERED=1 \
    UV_OFFLINE=0

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/neurolens

# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml ./
COPY requirements ./requirements
RUN pip install --no-cache-dir -r requirements/model.txt \
    && pip install --no-cache-dir uv

COPY worker.py config.json ./
COPY neurolens ./neurolens

# The warm-up clip (3 s of the Sintel trailer, data/videos/SOURCES.md).
COPY data/warmup.mp4 ./data/warmup.mp4

# config.json's ./models is the weights SageMaker copies to /opt/ml/model at instance start.
# The area maps (data/hcp_mmp) travel in the same prefix and are read through ./data, so link
# that one folder too; the warm-up clip stays in the image.
RUN mkdir -p /opt/ml/model /opt/neurolens/data \
    && ln -s /opt/ml/model /opt/neurolens/models \
    && ln -s /opt/ml/model/data/hcp_mmp /opt/neurolens/data/hcp_mmp

# The WhisperX environment tribev2 runs at run time (`uvx whisperx <wav> ...` in
# tribev2/eventstransforms.py at the pinned commit). uvx keys its cached environment on the tool
# name, so any `uvx whisperx` builds the same one; UV_OFFLINE=1 then reuses it. Its model weights
# are not in the image: they live under HF_HOME/TORCH_HOME, which settings.py points at ./models.
RUN uvx whisperx --help

ENV UV_OFFLINE=1

ENTRYPOINT ["python", "/opt/neurolens/worker.py", "serve"]
