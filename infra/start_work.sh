#!/usr/bin/env bash
# Starts a work session (M2a §4d, M2b §2): checks the idle alarm, starts the NAT instance, then
# lets the queue start workers.
#   infra/start_work.sh                      NAT on, worker group max 1: an upload starts a worker
#   infra/start_work.sh --max 2              the same with up to two workers (Experiment 2 only)
#   infra/start_work.sh --worker [--hours N] a warm hold: one worker starts now and stays for N hours
#                                            (1 to 4, default 3), even with no jobs; for demos, study
#                                            sessions and manual work. Run it again to move the end.
# A GPU worker costs $1.86 an hour ($2.24 if only a 2xlarge is free). End every session with
# infra/stop_work.sh.
set -euo pipefail
source "$(dirname "$0")/aws_env.sh"   # AWS_PROFILE, AWS_REGION (M2a §4i)
ASG=neurolens-workers
HOLD_END=neurolens-warm-hold-end
ALARMS="neurolens-worker-idle neurolens-worker-scale-out neurolens-worker-scale-in"
BREAKER_RULE=neurolens-breaker-every-5-min
REAPER_RULE=neurolens-reaper-every-5-min

usage() { echo "usage: $0 [--max 1|2] [--worker [--hours 1-4]]" >&2; exit 2; }
WORKER=0 HOURS=3 MAX=1
while [ $# -gt 0 ]; do
  case "$1" in
    --worker) WORKER=1 ;;
    --hours) [ $# -ge 2 ] || usage; HOURS=$2; shift ;;
    --max) [ $# -ge 2 ] || usage; MAX=$2; shift ;;
    *) usage ;;
  esac
  shift
done
# The 4-hour cap bounds a forgotten hold (about $7.50): a hold switches off every automatic stop.
case "$HOURS" in 1|2|3|4) ;; *) echo "--hours must be 1, 2, 3 or 4 (got $HOURS)." >&2; exit 2 ;; esac
case "$MAX" in 1|2) ;; *) echo "--max must be 1 or 2 (got $MAX)." >&2; exit 2 ;; esac

# No session starts unless every money guard is in place: the three alarms with their actions on
# (idle, scale-out, scale-in) and the circuit breaker's 5-minute schedule enabled (M2b §2).
# shellcheck disable=SC2086  # ALARMS is a space-separated list on purpose
if ! aws cloudwatch enable-alarm-actions --alarm-names $ALARMS; then
  echo "Could not switch on the alarms' actions (above). If it says AccessDenied, paste the current" >&2
  echo "infra/iam/neurolens-deploy-services.json into the console policy. Nothing was started." >&2
  exit 1
fi
# shellcheck disable=SC2086
ON=$(aws cloudwatch describe-alarms --alarm-names $ALARMS \
  --query 'length(MetricAlarms[?ActionsEnabled])' --output text)
if [ "$ON" != 3 ]; then
  echo "Only $ON of the 3 alarms ($ALARMS) exist with their actions on: run terraform apply. Nothing was started." >&2
  exit 1
fi
if [ "$(aws events describe-rule --name "$BREAKER_RULE" --query State --output text 2>/dev/null)" != ENABLED ]; then
  echo "The circuit breaker's schedule $BREAKER_RULE is missing or disabled: run terraform apply. Nothing was started." >&2
  exit 1
fi

SIZES=$(aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG" \
  --query 'AutoScalingGroups[0].[MinSize,MaxSize,DesiredCapacity]' --output text)
if [ "$SIZES" = "None" ]; then
  echo "No worker group yet (it is created once worker_ami_id is set)." >&2
  [ "$WORKER" = 0 ] && [ "$MAX" = 1 ] || { echo "Cannot start workers without the group. Nothing was started." >&2; exit 1; }
  DES=0
else
  read -r MIN _ DES <<<"$SIZES"
  # The idle alarm stays in ALARM for 5-10 minutes after the breaker fires (until a 5-minute slice
  # with no worker in service). Meanwhile the breaker's schedule would set max 0 again, silently
  # undoing this start. A warm hold (minimum 1) is safe: the breaker leaves it alone.
  if [ "$WORKER" = 0 ] && [ "$MIN" = 0 ] && [ "$(aws cloudwatch describe-alarms \
      --alarm-names neurolens-worker-idle --query 'MetricAlarms[0].StateValue' --output text)" = ALARM ]; then
    echo "The idle alarm is still in ALARM (the circuit breaker stopped the workers): the breaker would" >&2
    echo "undo this start. Try again in about 10 minutes, or start a warm hold with --worker. Nothing was started." >&2
    exit 1
  fi
fi
# Never below the workers the group already has: a lower max makes AWS end one, even mid-job.
if [ "$MAX" -lt "$DES" ]; then
  echo "The worker group has $DES workers; max $MAX would end one, possibly mid-job. Wait until the" >&2
  echo "queue is empty (scale-in ends them), or end the session with infra/stop_work.sh. Nothing was started." >&2
  exit 1
fi

NAT=$(aws ec2 describe-instances \
  --filters Name=tag:Project,Values=neurolens Name=tag:Role,Values=nat \
            Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text)
[ -n "$NAT" ] || { echo "No NAT instance found (tag Role=nat): run terraform apply first." >&2; exit 1; }
read -r NAT_ID NAT_STATE <<<"$NAT"

# A group at max 0 while the NAT instance runs means the session was not ended by stop_work.sh:
# usually the circuit breaker stopped the workers (M2b §2c; its email says why).
if [ "$SIZES" != "None" ] && [ "$(echo "$SIZES" | awk '{print $2}')" = 0 ] \
    && [ "$NAT_STATE" = running ]; then
  echo "Note: the worker group was at max 0 with the NAT instance running. The circuit breaker"
  echo "probably stopped the workers after 90 minutes without queue activity (see the alarm email)."
fi

# The warm hold's end goes in first: if AWS cannot record it, nothing starts (a hold with no end
# would keep a GPU running until stop_work.sh).
if [ "$WORKER" = 1 ]; then
  END=$(date -u -v+"${HOURS}"H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
        || date -u -d "+${HOURS} hours" +%Y-%m-%dT%H:%M:%SZ)
  if ! aws autoscaling put-scheduled-update-group-action --auto-scaling-group-name "$ASG" \
      --scheduled-action-name "$HOLD_END" --start-time "$END" --min-size 0; then
    echo "Could not set the warm hold's end time (above). Nothing was started." >&2
    exit 1
  fi
fi

if [ "$NAT_STATE" != running ]; then
  aws ec2 start-instances --instance-ids "$NAT_ID" --output text >/dev/null
  aws ec2 wait instance-running --instance-ids "$NAT_ID"
fi
echo "NAT instance $NAT_ID running (about 0.84 cents an hour)."

# The reaper (M3a §7) settles or refunds stuck jobs, during sessions only: between them its
# 5-minute schedule would keep Aurora from pausing. stop_work.sh switches it off again.
if ! aws events enable-rule --name "$REAPER_RULE"; then
  echo "Could not enable the reaper's schedule $REAPER_RULE (above): run terraform apply, then this" >&2
  echo "script again. The NAT instance is running: end with infra/stop_work.sh if you stop here." >&2
  exit 1
fi
echo "Reaper on (every 5 minutes; keeps the database awake, about 6 cents an hour, until stop_work.sh)."
[ "$SIZES" != "None" ] || exit 0

if [ "$WORKER" = 1 ]; then
  aws autoscaling update-auto-scaling-group --auto-scaling-group-name "$ASG" \
    --min-size 1 --max-size "$MAX" --desired-capacity $((DES > 1 ? DES : 1))
  echo "Warm hold until $END UTC: one worker is starting or running (billed from now) and stays even with no jobs."
  echo "After that, AWS ends it once the queue has been empty for 15 minutes. Run again to extend."
else
  # Never touches the minimum or the hold's end, so it cannot end a running hold.
  aws autoscaling update-auto-scaling-group --auto-scaling-group-name "$ASG" --max-size "$MAX"
  echo "Worker group: max $MAX. An upload starts a worker by itself (about 6.5 minutes on GPU)."
fi
