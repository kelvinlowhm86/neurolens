#!/usr/bin/env bash
# systemd ExecStart for the GPU worker. Pulls the image tag currently in SSM (a release only adds the
# thin code layer — the rest is cached in the AMI) and runs the worker in the foreground.
set -euo pipefail

# Written by the launch template's user data; until then, fail and let systemd retry.
[[ -f /etc/neurolens/boot.env && -f /etc/neurolens/worker.env ]] || { echo "env files not written yet"; exit 1; }
source /etc/neurolens/boot.env          # ECR_REPO, IMAGE_TAG_PARAM, AWS_REGION

TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 300")
INSTANCE_ID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)

TAG=$(aws ssm get-parameter --region "$AWS_REGION" --name "$IMAGE_TAG_PARAM" --query Parameter.Value --output text)
IMAGE="$ECR_REPO:$TAG"
REGISTRY="${ECR_REPO%%/*}"

if aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY" \
   && docker pull "$IMAGE"; then
  echo "Pulled $IMAGE"
else
  # Registry unreachable: fall back to the newest worker image baked into the AMI.
  IMAGE=$(docker images --format '{{.Repository}}:{{.Tag}}' "$ECR_REPO" | head -1)
  echo "Pull failed; using baked image $IMAGE"
fi

docker rm -f neurolens-worker 2>/dev/null || true
exec docker run --name neurolens-worker --rm --gpus all --network host \
  --env-file /etc/neurolens/worker.env -e WORKER_IMAGE_TAG="$TAG" \
  -v /opt/neurolens/models:/models:ro -v /opt/neurolens/scratch:/scratch \
  --log-driver awslogs --log-opt awslogs-region="$AWS_REGION" \
  --log-opt awslogs-group=/neurolens/worker --log-opt awslogs-create-group=true \
  --log-opt awslogs-stream="$INSTANCE_ID" \
  "$IMAGE" run
