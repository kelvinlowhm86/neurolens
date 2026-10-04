#!/usr/bin/env bash
# Opens a shell on the running worker through Session Manager (no SSH), after a warning that AWS
# ends idle workers (M2b §2). Needs the AWS Session Manager plugin for the AWS CLI on this Mac.
set -euo pipefail
source "$(dirname "$0")/aws_env.sh"   # AWS_PROFILE, AWS_REGION (M2a §4i)
IDS=$(aws ec2 describe-instances \
  --filters Name=tag:Project,Values=neurolens Name=tag:Role,Values=worker Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text)
read -r ID REST <<<"$IDS"
case "$ID" in i-*) ;; *) echo "No running worker." >&2; exit 1 ;; esac
[ -z "${REST:-}" ] || echo "More than one worker is running ($IDS): connecting to $ID."
cat <<EOF

  AWS ends this worker 15 minutes after the job queue empties, unless a warm hold is on.
  For manual work, run infra/start_work.sh --worker --hours N first (1 to 4 hours; run it again
  to extend).

EOF
exec aws ssm start-session --target "$ID"
