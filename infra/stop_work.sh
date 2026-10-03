#!/usr/bin/env bash
# Ends a work session (M2a §4d): worker group to zero, waits for workers to go, stops the NAT
# instance, then checks that no neurolens machine is left running. Run at the end of every session.
set -euo pipefail
source "$(dirname "$0")/aws_env.sh"   # AWS_PROFILE, AWS_REGION (M2a §4i)
ASG=neurolens-workers

live_instances() {  # $1: extra filters, e.g. Name=tag:Role,Values=worker
  # shellcheck disable=SC2086
  aws ec2 describe-instances \
    --filters Name=tag:Project,Values=neurolens Name=instance-state-name,Values=pending,running,stopping,shutting-down $1 \
    --query 'Reservations[].Instances[].[InstanceId,InstanceType,Tags[?Key==`Name`]|[0].Value,State.Name]' --output text
}

if [ -n "$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG" \
      --query 'AutoScalingGroups[].AutoScalingGroupName' --output text)" ]; then
  aws autoscaling update-auto-scaling-group --auto-scaling-group-name "$ASG" \
    --min-size 0 --max-size 0 --desired-capacity 0
  echo "Worker group set to 0. Waiting for workers to terminate..."
  for _ in $(seq 1 60); do
    [ -z "$(live_instances Name=tag:Role,Values=worker)" ] && break
    sleep 10
  done
fi

NAT=$(aws ec2 describe-instances \
  --filters Name=tag:Project,Values=neurolens Name=tag:Role,Values=nat Name=instance-state-name,Values=pending,running \
  --query 'Reservations[].Instances[].InstanceId' --output text)
if [ -n "$NAT" ]; then
  aws ec2 stop-instances --instance-ids "$NAT" --output text >/dev/null
  aws ec2 wait instance-stopped --instance-ids "$NAT"
  echo "NAT instance $NAT stopped."
fi

LEFT=$(live_instances "")
if [ -n "$LEFT" ]; then
  echo "WARNING: these neurolens machines are still running or stopping (and may be billing):" >&2
  echo "$LEFT" >&2
  exit 1
fi
echo "ALL STOPPED: no neurolens machine is running."
