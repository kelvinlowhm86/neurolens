#!/usr/bin/env bash
# Runs on the Packer builder (root). Bakes the worker image layers + all model weights into the AMI.
set -euo pipefail

: "${WORKER_IMAGE:?}" "${ECR_PASSWORD:?}" "${HF_TOKEN:?}"
REGISTRY="${WORKER_IMAGE%%/*}"
MODELS=/opt/neurolens/models
APP_UID=10001                                  # the worker image runs as this user

echo "== 1/5 Pull the worker image (its layers stay cached in the AMI)"
echo "$ECR_PASSWORD" | docker login --username AWS --password-stdin "$REGISTRY"
docker pull "$WORKER_IMAGE"

echo "== 2/5 Download every model weight into $MODELS"
mkdir -p "$MODELS" /opt/neurolens/scratch
chown -R "$APP_UID" "$MODELS" /opt/neurolens/scratch
docker run --rm --gpus all \
  -e HF_TOKEN="$HF_TOKEN" -e HF_HUB_OFFLINE=0 \
  -v "$MODELS":/models -v /opt/neurolens/scratch:/scratch \
  -v /tmp/neurolens/sample.mp4:/sample.mp4:ro \
  "$WORKER_IMAGE" download-weights --sample /sample.mp4

echo "== 3/5 Acceptance test: a full prediction with the network weights frozen (HF_HUB_OFFLINE=1)"
docker run --rm --gpus all \
  -e HF_HUB_OFFLINE=1 \
  -v "$MODELS":/models:ro -v /opt/neurolens/scratch:/scratch \
  -v /tmp/neurolens/sample.mp4:/sample.mp4:ro \
  "$WORKER_IMAGE" warmup --sample /sample.mp4

echo "== 4/5 Install the boot script + systemd unit"
install -m 0755 /tmp/neurolens/neurolens-boot.sh /usr/local/bin/neurolens-boot.sh
install -m 0755 /tmp/neurolens/neurolens-scratch.sh /usr/local/bin/neurolens-scratch.sh
install -m 0644 /tmp/neurolens/neurolens-worker.service /etc/systemd/system/neurolens-worker.service
systemctl daemon-reload
systemctl enable neurolens-worker.service

echo "== 5/5 Clean up credentials and scratch"
docker logout "$REGISTRY"
rm -rf /root/.docker/config.json /tmp/neurolens /opt/neurolens/scratch/*
unset HF_TOKEN ECR_PASSWORD
du -sh "$MODELS"
echo "Bake complete."
