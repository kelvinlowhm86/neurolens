#!/usr/bin/env bash
# Opens a shell on the running worker through Session Manager (no SSH), after the idle-alarm warning
# (M2a §4f). Needs the AWS Session Manager plugin for the AWS CLI on this Mac.
set -euo pipefail
source "$(dirname "$0")/aws_env.sh"   # AWS_PROFILE, AWS_REGION (M2a §4i)
IDS=$(aws ec2 describe-instances \
  --filters Name=tag:Project,Values=neurolens Name=tag:Role,Values=worker Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text)
read -r ID REST <<<"$IDS"
case "$ID" in i-*) ;; *) echo "No running worker." >&2; exit 1 ;; esac
[ -z "${REST:-}" ] || echo "More than one worker is running ($IDS): connecting to $ID."
cat <<EOF

  This worker is ended automatically after 90 minutes without queue jobs (the idle alarm).
  For longer manual work, run infra/pause_idle_alarm.sh first (it lasts until the session ends).

EOF
exec aws ssm start-session --target "$ID"
