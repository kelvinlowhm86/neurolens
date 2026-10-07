#!/usr/bin/env bash
# Opens a shell on the running worker through Session Manager (no SSH). Needs the AWS Session
# Manager plugin for the AWS CLI on this Mac.
# Refuses unless a warm hold is on: otherwise AWS ends the worker 15 minutes after the job queue
# empties, in the middle of the session (M2b §2).
set -euo pipefail
source "$(dirname "$0")/aws_env.sh"   # AWS_PROFILE, AWS_REGION (M2a §4i)
ASG=neurolens-workers

MIN=$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG" \
  --query 'AutoScalingGroups[0].MinSize' --output text)
case "$MIN" in
  ''|None) echo "No worker group $ASG in $AWS_REGION." >&2; exit 1 ;;
  0) echo "No warm hold is on, so AWS could end the worker mid-session. Run infra/start_work.sh --keep-worker first." >&2; exit 1 ;;
esac
IDS=$(aws ec2 describe-instances \
  --filters Name=tag:Project,Values=neurolens Name=tag:Role,Values=worker Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text)
read -r ID REST <<<"$IDS"
case "$ID" in i-*) ;; *) echo "No running worker yet (a warm hold starts one in about 6.5 minutes)." >&2; exit 1 ;; esac
[ -z "${REST:-}" ] || echo "More than one worker is running ($IDS): connecting to $ID."
exec aws ssm start-session --target "$ID"
