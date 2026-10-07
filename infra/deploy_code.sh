#!/usr/bin/env bash
# Ships the last commit to S3 as the worker code bundle (M2a §5), then restarts every running worker
# that has no job, so it runs the new code (M3b §5). A worker with a job is named and left alone: it
# gets the new code at its next start. New workers pull the latest code on start.
#   infra/deploy_code.sh        restarts idle workers only
#   infra/deploy_code.sh --now  restarts busy ones too (their job is handed back and retried: the GPU
#                               time so far is lost and one of the job's 2 receives is used up)
set -euo pipefail
NOW=0
case "${1:-}" in '') ;; --now) NOW=1 ;; *) echo "usage: $0 [--now]" >&2; exit 2 ;; esac
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
REVISION=$(cat "$TMP/code.revision")
echo "Uploaded $REVISION."

IDS=$(aws ec2 describe-instances \
  --filters Name=tag:Project,Values=neurolens Name=tag:Role,Values=worker Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text)
if [ -z "$IDS" ]; then
  echo "No running workers: nothing to restart. New workers pull $REVISION on start."; exit 0
fi
# A worker holds scale-in protection exactly while it has a job (M2b §2c).
BUSY=$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names neurolens-workers \
  --query 'AutoScalingGroups[0].Instances[?ProtectedFromScaleIn].InstanceId' --output text | tr '\t' ' ')
[ "$BUSY" != None ] || BUSY=""
TARGETS=""
for ID in $IDS; do
  case " $BUSY " in
    *" $ID "*) [ "$NOW" = 1 ] || { echo "$ID has a job: left alone, it gets $REVISION at its next start (or run with --now)."; continue; } ;;
  esac
  TARGETS="$TARGETS $ID"
done
[ -n "$TARGETS" ] || exit 0

# reset-failed first, so deploys never count toward the crash limit (3 starts an hour, M2a §3).
# shellcheck disable=SC2086  # TARGETS is a space-separated list on purpose
CMD=$(aws ssm send-command --instance-ids $TARGETS --document-name AWS-RunShellScript \
  --comment "neurolens deploy_code" \
  --parameters 'commands=["systemctl reset-failed neurolens-worker","systemctl restart neurolens-worker","systemctl is-active neurolens-worker","cat /opt/neurolens/app/REVISION"]' \
  --query Command.CommandId --output text)
FAILED=0
for ID in $TARGETS; do
  aws ssm wait command-executed --command-id "$CMD" --instance-id "$ID" || true
  RESULT=$(aws ssm get-command-invocation --command-id "$CMD" --instance-id "$ID" \
    --query '[Status,StandardOutputContent]' --output text | tr '\n' ' ')
  echo "$ID: $RESULT"
  case "$RESULT" in Success*) ;; *) FAILED=1 ;; esac
done
[ "$FAILED" = 0 ] || { echo "A restart did not succeed (above): that worker may still run the old code." >&2; exit 1; }
