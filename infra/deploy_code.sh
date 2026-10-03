#!/usr/bin/env bash
# Ships the last commit to S3 as the worker code bundle (M2a §5). Running workers pick it up on
# their next restart (infra/restart_workers.sh); new ones on start.
set -euo pipefail
source "$(dirname "$0")/aws_env.sh"   # AWS_PROFILE, AWS_REGION (M2a §4i)
cd "$(git rev-parse --show-toplevel)"

if [ -n "$(git status --porcelain)" ]; then
  echo "Uncommitted changes: commit first. Deploy ships the last commit only." >&2; exit 1
fi
BUCKET=$(terraform -chdir=infra/terraform output -raw bucket_name)

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
git archive --format=zip -o "$TMP/code.zip" HEAD worker.py neurolens pyproject.toml requirements \
  infra/pull_code.sh infra/self_terminate.sh infra/neurolens-worker.service infra/neurolens-self-terminate.service
git rev-parse --short HEAD > "$TMP/code.revision"
aws s3 cp "$TMP/code.zip"      "s3://$BUCKET/code/latest.zip" --only-show-errors
aws s3 cp "$TMP/code.revision" "s3://$BUCKET/code/latest.revision" --only-show-errors
echo "Deployed $(cat "$TMP/code.revision"). Running workers pick it up on their next restart; new ones on start."
