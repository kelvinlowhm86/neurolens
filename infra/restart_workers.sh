#!/usr/bin/env bash
# Restarts the worker service on every running worker through SSM Run Command (M2a §5), so a
# deploy reaches them without new machines. Prints the code revision each one now runs.
# reset-failed first, so deploys never count toward the crash limit (3 starts an hour, M2a §3).
set -euo pipefail
source "$(dirname "$0")/aws_env.sh"   # AWS_PROFILE, AWS_REGION (M2a §4i)

IDS=$(aws ec2 describe-instances \
  --filters Name=tag:Project,Values=neurolens Name=tag:Role,Values=worker Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text)
if [ -z "$IDS" ]; then
  echo "No running workers: nothing to restart. New workers pull the latest code on start."; exit 0
fi

# shellcheck disable=SC2086  # IDS is a space-separated list on purpose
CMD=$(aws ssm send-command --instance-ids $IDS --document-name AWS-RunShellScript \
  --comment "neurolens restart_workers" \
  --parameters 'commands=["systemctl reset-failed neurolens-worker","systemctl restart neurolens-worker","systemctl is-active neurolens-worker","cat /opt/neurolens/app/REVISION"]' \
  --query Command.CommandId --output text)

for ID in $IDS; do
  aws ssm wait command-executed --command-id "$CMD" --instance-id "$ID" || true
  echo "$ID: $(aws ssm get-command-invocation --command-id "$CMD" --instance-id "$ID" \
    --query '[Status,StandardOutputContent]' --output text | tr '\n' ' ')"
done
