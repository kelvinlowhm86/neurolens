#!/usr/bin/env bash
# Restarts the worker service on every running worker through SSM Run Command (M2a §5), so a
# deploy reaches them without new machines. Prints the code revision each one now runs.
# reset-failed first, so deploys never count toward the crash limit (3 starts an hour, M2a §3).
#   infra/restart_workers.sh        refuses while a worker has a job (it would be handed back, its GPU
#                                   time lost and one of its 2 receives used up)
#   infra/restart_workers.sh --now  restarts anyway
set -euo pipefail
source "$(dirname "$0")/aws_env.sh"   # AWS_PROFILE, AWS_REGION (M2a §4i)
NOW=0
case "${1:-}" in '') ;; --now) NOW=1 ;; *) echo "usage: $0 [--now]" >&2; exit 2 ;; esac

IDS=$(aws ec2 describe-instances \
  --filters Name=tag:Project,Values=neurolens Name=tag:Role,Values=worker Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text)
if [ -z "$IDS" ]; then
  echo "No running workers: nothing to restart. New workers pull the latest code on start."; exit 0
fi
# A worker holds scale-in protection exactly while it has a job (M2b §2c).
BUSY=$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names neurolens-workers \
  --query 'AutoScalingGroups[0].Instances[?ProtectedFromScaleIn].InstanceId' --output text)
if [ -n "$BUSY" ] && [ "$BUSY" != None ] && [ "$NOW" = 0 ]; then
  echo "Busy with a job: $BUSY. Wait until it finishes, or run again with --now. Nothing was restarted." >&2
  exit 1
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
