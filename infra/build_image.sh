#!/usr/bin/env bash
# Builds the SageMaker container image (docs/M3e_spec.md §4d): zips the repository at HEAD,
# uploads it to s3://<bucket>/build/source.zip, starts the CodeBuild project, waits, and prints
# the image digest. A deploy is: this script, put the digest in terraform.tfvars, terraform apply.
set -euo pipefail

ROOT=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)
cd "$ROOT"

if [ -n "$(git status --porcelain)" ]; then
  echo "Refusing to build from a dirty tree: commit or stash first (the build uses HEAD)." >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$ROOT/infra/aws_env.sh"

tf() { terraform -chdir="$ROOT/infra/terraform" output -raw "$1"; }
BUCKET=$(tf bucket_name)
PROJECT=$(tf worker_image_build_project)
REPO=$(tf worker_ecr_repository)
REPO_NAME=${REPO##*/}
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
TAG=$(git rev-parse --short=12 HEAD)

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
git archive --format=zip --output="$TMP/source.zip" HEAD -- \
  neurolens requirements pyproject.toml config.json worker.py \
  data/warmup.mp4 infra/worker_image
aws s3 cp "$TMP/source.zip" "s3://$BUCKET/build/source.zip" --only-show-errors

BUILD_ID=$(aws codebuild start-build \
  --project-name "$PROJECT" \
  --environment-variables-override \
    "name=IMAGE_TAG,value=$TAG,type=PLAINTEXT" \
    "name=AWS_ACCOUNT_ID,value=$ACCOUNT_ID,type=PLAINTEXT" \
  --query 'build.id' --output text)
echo "Started $PROJECT ($BUILD_ID) for $REPO:$TAG"

while :; do
  STATUS=$(aws codebuild batch-get-builds --ids "$BUILD_ID" --query 'builds[0].buildStatus' --output text)
  case "$STATUS" in
    SUCCEEDED) break ;;
    FAILED|FAULT|STOPPED|TIMED_OUT)
      echo "Build $STATUS. Logs:" >&2
      aws codebuild batch-get-builds --ids "$BUILD_ID" --query 'builds[0].logs.deepLink' --output text >&2
      exit 1
      ;;
    IN_PROGRESS|QUEUED) sleep 15 ;;
    *) echo "Unexpected build status: $STATUS" >&2; exit 1 ;;
  esac
done

DIGEST=$(aws ecr describe-images --repository-name "$REPO_NAME" --image-ids imageTag="$TAG" \
  --query 'imageDetails[0].imageDigest' --output text)
echo "Image:  $REPO:$TAG"
echo "Digest: $DIGEST"
echo
echo "Set worker_image = \"$DIGEST\" in infra/terraform/terraform.tfvars, then:"
echo "  terraform -chdir=infra/terraform apply"
