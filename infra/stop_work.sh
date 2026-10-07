#!/usr/bin/env bash
# Ends a work session (M2a §4d, M2b §2d). Run at the end of every session.
# Re-enables the alarms' actions, removes a warm hold's end timer and the workers' scale-in protection
# (a stop means stop: a running job is handed back for the next session), sets the worker group to
# 0/0/0 (even while it is still waiting for GPU capacity and has no machine yet), waits for workers
# to go, stops the NAT instance, then checks. It prints ALL STOPPED only when every check succeeded;
# anything unproven prints NOT CONFIRMED and exits 1. An image-build machine is listed, not stopped (it may be running on
# purpose; it has its own guards).
set -uo pipefail   # no -e on purpose: a failed call is recorded and the script goes on stopping the rest
ASG=neurolens-workers
ALARMS="neurolens-worker-idle neurolens-worker-scale-out neurolens-worker-scale-in"
HOLD_END=neurolens-warm-hold-end
REAPER=neurolens-reaper
REAPER_RULE=neurolens-reaper-every-5-min
DLQ=neurolens-jobs-dlq
DLQ_HANDLER=neurolens-dlq-handler
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
hold_end() {  # the warm hold's end timer: its name, or "" if there is none; fails if the call fails
  aws autoscaling describe-scheduled-actions --auto-scaling-group-name "$ASG" \
    --scheduled-action-names "$HOLD_END" --query 'ScheduledUpdateGroupActions[].ScheduledActionName' --output text
}
group_machines() {  # how many machines the group still tracks (it knows a launch before EC2 lists it)
  aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG" \
    --query 'length(AutoScalingGroups[0].Instances)' --output text
}

# 1. The alarms act in every session, even if someone switched them off by hand.
# shellcheck disable=SC2086  # ALARMS is a space-separated list on purpose
aws cloudwatch enable-alarm-actions --alarm-names $ALARMS \
  || problem "could not re-enable the alarms' actions ($ALARMS)"
# AWS accepts a missing alarm's name without error, so count the ones that exist with actions on.
# shellcheck disable=SC2086
ON=$(aws cloudwatch describe-alarms --alarm-names $ALARMS \
  --query 'length(MetricAlarms[?ActionsEnabled])' --output text)
[ "$ON" = 3 ] || problem "only ${ON:-0} of the 3 alarms ($ALARMS) exist with their actions on: run terraform apply"

# 1b. A warm hold's end timer would otherwise fire in a later session (M2b §2d).
if ! HOLD=$(hold_end); then
  problem "could not look for the warm hold's end timer ($HOLD_END)"
elif [ -n "$HOLD" ] && [ "$HOLD" != "None" ]; then
  aws autoscaling delete-scheduled-action --auto-scaling-group-name "$ASG" --scheduled-action-name "$HOLD_END" \
    && echo "Warm hold ended." || problem "could not delete the warm hold's end timer ($HOLD_END)"
fi

# 2. Worker group to 0/0/0 whenever any number is above 0, machines or not.
if ! SIZES=$(group_sizes); then
  problem "could not read the worker group $ASG"
elif [ "$SIZES" = "None" ]; then
  # The group exists since worker_ami_id was set, so its absence means a wrong region or a broken setup.
  problem "no worker group $ASG in $AWS_REGION: is this the right region?"
else
  read -r MIN MAX DES <<<"$SIZES"
  # Max 0 while the NAT instance runs: the session was not ended by this script, usually because the
  # circuit breaker stopped the workers (M2b §2c; its email says why). A note, not a problem.
  if [ "$MAX" = 0 ] && [ -n "$(aws ec2 describe-instances --filters Name=tag:Project,Values=neurolens \
      Name=tag:Role,Values=nat Name=instance-state-name,Values=running \
      --query 'Reservations[].Instances[].InstanceId' --output text 2>/dev/null)" ]; then
    echo "Note: the worker group was already at max 0 with the NAT instance running. The circuit"
    echo "breaker probably stopped the workers during this session (see the alarm email)."
  fi
  # A busy worker protects itself from scale-in (M2b §1a); without this it would outlive the stop.
  if ! IDS=$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG" \
      --query 'AutoScalingGroups[0].Instances[].InstanceId' --output text); then
    problem "could not list the workers' scale-in protection"
  elif [ -n "$IDS" ] && [ "$IDS" != "None" ]; then
    # shellcheck disable=SC2086  # IDS is a space-separated list on purpose
    aws autoscaling set-instance-protection --auto-scaling-group-name "$ASG" --instance-ids $IDS \
      --no-protected-from-scale-in \
      || problem "could not remove the workers' scale-in protection ($IDS): they may not end"
  fi
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

# 2b. Reaper (M3a §7): one last run, so jobs left stuck in this session are settled or refunded now,
# then its schedule off, so Aurora can pause (about 10 minutes later) until the next session.
REAPER_OUT=$(mktemp)
if ! REAPER_ERR=$(aws lambda invoke --function-name "$REAPER" --cli-read-timeout 150     --query FunctionError --output text "$REAPER_OUT"); then
  problem "could not run the reaper $REAPER (above)"
elif [ "$REAPER_ERR" != "None" ]; then
  problem "the reaper failed ($REAPER_ERR): $(head -c 300 "$REAPER_OUT"); see its log /aws/lambda/$REAPER"
else
  echo "Reaper ran once (stuck jobs settled or refunded)."
fi
rm -f "$REAPER_OUT"
aws events disable-rule --name "$REAPER_RULE" \
  || problem "could not disable the reaper's schedule $REAPER_RULE: Aurora will not pause"

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

# 4. Check: no hold timer, the reaper's schedule off, the dead-letter queue empty, the group reads
# 0/0/0 and no neurolens machine may be billing.
if ! RULE_STATE=$(aws events describe-rule --name "$REAPER_RULE" --query State --output text); then
  problem "could not read the reaper's schedule $REAPER_RULE (if it does not exist yet: terraform apply)"
elif [ "$RULE_STATE" != DISABLED ]; then
  problem "the reaper's schedule $REAPER_RULE is $RULE_STATE: Aurora will not pause"
fi
# A message still in the dead-letter queue means the refund handler has not finished with it. It
# retries every 12 minutes for up to 14 days, waking Aurora each time (M3a §7).
if ! DLQ_URL=$(aws sqs get-queue-url --queue-name "$DLQ" --query QueueUrl --output text) \
    || ! DLQ_COUNTS=$(aws sqs get-queue-attributes --queue-url "$DLQ_URL" --attribute-names \
      ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible \
      --query 'Attributes.[ApproximateNumberOfMessages,ApproximateNumberOfMessagesNotVisible]' --output text); then
  problem "could not read the dead-letter queue $DLQ"
elif [ "$(echo "$DLQ_COUNTS" | awk '{print $1 + $2}')" != 0 ]; then
  problem "the dead-letter queue $DLQ holds messages (waiting, in progress: $DLQ_COUNTS): the refund
      handler has not settled them, and each retry wakes Aurora. Run this script again in a few minutes;
      if they stay, see the log /aws/lambda/$DLQ_HANDLER"
fi
if ! HOLD=$(hold_end); then
  problem "could not re-check the warm hold's end timer"
elif [ -n "$HOLD" ] && [ "$HOLD" != "None" ]; then
  problem "the warm hold's end timer $HOLD_END still exists"
fi
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
echo "ALL STOPPED in $AWS_REGION: worker group at 0, no warm hold, reaper off, dead-letter queue empty, no neurolens machine running."
echo "Aurora pauses by itself about 10 minutes after its last use."
