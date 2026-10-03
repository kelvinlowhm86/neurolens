#!/usr/bin/env bash
# Starts a work session (M2a §4d): the NAT instance, then lets the worker group run one machine.
#   infra/start_work.sh            NAT on, worker group max 1 (no worker yet)
#   infra/start_work.sh --worker   also start one on-demand GPU worker ($1.86 an hour; $2.24 if only a 2xlarge is free)
# End every session with infra/stop_work.sh.
set -euo pipefail
export AWS_PROFILE="${NEUROLENS_AWS_PROFILE:-neurolens}" AWS_REGION=us-east-1
ASG=neurolens-workers

WORKER=0
case "${1:-}" in
  --worker) WORKER=1 ;;
  "") ;;
  *) echo "usage: $0 [--worker]" >&2; exit 2 ;;
esac

NAT=$(aws ec2 describe-instances \
  --filters Name=tag:Project,Values=neurolens Name=tag:Role,Values=nat \
            Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query 'Reservations[].Instances[].InstanceId' --output text)
[ -n "$NAT" ] || { echo "No NAT instance found (tag Role=nat): run terraform apply first." >&2; exit 1; }

aws ec2 start-instances --instance-ids "$NAT" --output text >/dev/null
aws ec2 wait instance-running --instance-ids "$NAT"
echo "NAT instance $NAT running (about 0.84 cents an hour)."

if [ -z "$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG" \
      --query 'AutoScalingGroups[].AutoScalingGroupName' --output text)" ]; then
  echo "No worker group yet (it is created once worker_ami_id is set)."
  [ "$WORKER" = 0 ] || { echo "Cannot start a worker without the group." >&2; exit 1; }
  exit 0
fi

if [ "$WORKER" = 1 ]; then
  aws autoscaling update-auto-scaling-group --auto-scaling-group-name "$ASG" \
    --min-size 0 --max-size 1 --desired-capacity 1
  echo "Worker group: max 1, desired 1. A worker is starting (billed from now; it ends itself after 30 idle minutes)."
else
  aws autoscaling update-auto-scaling-group --auto-scaling-group-name "$ASG" --min-size 0 --max-size 1
  echo "Worker group: max 1. No worker started (use --worker)."
fi
