#!/usr/bin/env bash
# Ends a work session (M2a §4d). Run at the end of every session.
# Re-enables the idle alarm's action, sets the worker group to 0/0/0 (even while it is still waiting
# for GPU capacity and has no machine yet), waits for workers to go, stops the NAT instance, then
# checks. It prints ALL STOPPED only when every check succeeded; anything unproven prints
# NOT CONFIRMED and exits 1. An image-build machine is listed, not stopped (it may be running on
# purpose; it has its own guards).
set -uo pipefail   # no -e on purpose: a failed call is recorded and the script goes on stopping the rest
ASG=neurolens-workers
IDLE_ALARM=neurolens-worker-idle
PROBLEMS=""
problem() { PROBLEMS="$PROBLEMS  - $*"$'\n'; echo "PROBLEM: $*" >&2; }

# The region comes from Terraform (M2a §4i). Without it nothing can be checked: say so, never guess.
if ! AWS_REGION=$(bash -c 'source "$1" >/dev/null && printf %s "$AWS_REGION"' _ "$(dirname "$0")/aws_env.sh"); then
  echo "NOT CONFIRMED: nothing was checked or stopped, because the region is unknown (see above)." >&2
  echo "Fix that and run this script again. Meanwhile the idle alarm ends an idle worker within about 90 minutes." >&2
  exit 1
fi
export AWS_PROFILE="${NEUROLENS_AWS_PROFILE:-neurolens}" AWS_REGION

live() {  # $1: an extra filter or "". Neurolens machines that may be billing; fails if the call fails.
  # shellcheck disable=SC2086
  aws ec2 describe-instances \
    --filters Name=tag:Project,Values=neurolens Name=instance-state-name,Values=pending,running,stopping,shutting-down $1 \
    --query 'Reservations[].Instances[].[InstanceId,InstanceType,Tags[?Key==`Role`]|[0].Value,State.Name]' --output text
}
group_sizes() {  # "min max desired", "None" if there is no group; fails if the call fails
  aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG" \
    --query 'AutoScalingGroups[0].[MinSize,MaxSize,DesiredCapacity]' --output text
}
group_machines() {  # how many machines the group still tracks (it knows a launch before EC2 lists it)
  aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG" \
    --query 'length(AutoScalingGroups[0].Instances)' --output text
}

# 1. A pause (infra/pause_idle_alarm.sh) lasts until the end of the session.
aws cloudwatch enable-alarm-actions --alarm-names "$IDLE_ALARM" \
  || problem "could not re-enable the idle alarm's action ($IDLE_ALARM)"

# 2. Worker group to 0/0/0 whenever any number is above 0, machines or not.
if ! SIZES=$(group_sizes); then
  problem "could not read the worker group $ASG"
elif [ "$SIZES" = "None" ]; then
  # The group exists since worker_ami_id was set, so its absence means a wrong region or a broken setup.
  problem "no worker group $ASG in $AWS_REGION: is this the right region?"
else
  read -r MIN MAX DES <<<"$SIZES"
  if [ "$MIN $MAX $DES" != "0 0 0" ]; then
    if aws autoscaling update-auto-scaling-group --auto-scaling-group-name "$ASG" \
        --min-size 0 --max-size 0 --desired-capacity 0; then
      echo "Worker group set to 0 (was min $MIN, max $MAX, desired $DES)."
    else
      problem "could not set the worker group $ASG to 0"
      DES=skip   # workers will not leave: do not wait for them
    fi
  fi
  # Wait until the group tracks no machine and EC2 lists no live worker: a launch that was still
  # starting when the group went to 0 shows up in the group's list first.
  [ "$DES" = skip ] || for _ in $(seq 1 60); do   # up to 10 minutes
    if ! TRACKED=$(group_machines) || ! WORKERS=$(live Name=tag:Role,Values=worker); then
      problem "could not list the workers"; break
    fi
    [ "$TRACKED" = 0 ] && [ -z "$WORKERS" ] && break
    echo "Waiting for workers to end ($TRACKED tracked by the group): $(echo "$WORKERS" | awk '{print $1, $4}' | tr '\n' ' ')"
    sleep 10
  done
fi

# 3. NAT instance. It exists in every state but terminated; none at all means a wrong region.
if ! ANY_NAT=$(aws ec2 describe-instances \
    --filters Name=tag:Project,Values=neurolens Name=tag:Role,Values=nat \
              Name=instance-state-name,Values=pending,running,stopping,stopped \
    --query 'Reservations[].Instances[].InstanceId' --output text); then
  problem "could not look for the NAT instance"
elif [ -z "$ANY_NAT" ]; then
  problem "no NAT instance in $AWS_REGION: is this the right region?"
fi
if ! NAT=$(aws ec2 describe-instances \
    --filters Name=tag:Project,Values=neurolens Name=tag:Role,Values=nat Name=instance-state-name,Values=pending,running \
    --query 'Reservations[].Instances[].InstanceId' --output text); then
  problem "could not look for the NAT instance"
elif [ -n "$NAT" ]; then
  # shellcheck disable=SC2086  # NAT is a space-separated list on purpose
  if aws ec2 stop-instances --instance-ids $NAT --output text >/dev/null \
      && aws ec2 wait instance-stopped --instance-ids $NAT; then
    echo "NAT instance $NAT stopped."
  else
    problem "could not stop the NAT instance $NAT"
  fi
fi

# 4. Check: the group reads 0/0/0 and no neurolens machine may be billing.
if ! SIZES=$(group_sizes); then
  problem "could not re-read the worker group $ASG"
elif [ "$SIZES" != "None" ] && [ "$(echo "$SIZES" | tr -s ' \t' ' ')" != "0 0 0" ]; then
  problem "worker group $ASG is not at 0 (min max desired: $SIZES)"
fi
if ! LEFT=$(live ""); then
  problem "could not list the neurolens machines"
elif [ -n "$LEFT" ]; then
  while read -r ID TYPE ROLE STATE; do
    if [ "$ROLE" = "build" ]; then
      problem "image-build machine $ID ($TYPE) is $STATE. If no build is running, end it with:
      AWS_PROFILE=$AWS_PROFILE aws ec2 terminate-instances --instance-ids $ID --region $AWS_REGION"
    else
      problem "$ID ($TYPE, role $ROLE) is $STATE"
    fi
  done <<<"$LEFT"
fi
STOPPED=$(aws ec2 describe-instances \
  --filters Name=tag:Project,Values=neurolens Name=instance-state-name,Values=stopped \
  --query 'Reservations[].Instances[].[InstanceId,InstanceType,Tags[?Key==`Role`]|[0].Value]' --output text 2>/dev/null) \
  && [ -n "$STOPPED" ] && echo "Stopped (disk only, a few cents a month): $(echo "$STOPPED" | tr '\t\n' '  ')"

if [ -n "$PROBLEMS" ]; then
  printf 'NOT CONFIRMED in %s:\n%s' "$AWS_REGION" "$PROBLEMS" >&2
  exit 1
fi
echo "ALL STOPPED in $AWS_REGION: worker group at 0, no neurolens machine running."
