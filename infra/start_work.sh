#!/usr/bin/env bash
# Starts a work session (M2a §4d, M2b §2, M3b §5): checks the money guards and the NAT Gateway, then
# lets the queue start workers.
#   infra/start_work.sh                         worker group max 1: an upload starts a worker
#   infra/start_work.sh --max 2                 the same with up to two workers (Experiment 2 only;
#                                               deleted with the experiment tools after the report)
#   infra/start_work.sh --keep-worker [--hours N]
#                                               a warm hold: one worker starts now and stays for N
#                                               hours (1 to 4, default 3), even with no jobs. When
#                                               working alone, for example debugging. Run it again to
#                                               move the end.
#   infra/start_work.sh --keep-worker-and-db [--hours N]
#                                               the same, and Aurora kept awake (minimum 0.5 ACU, about
#                                               6 cents an hour) so nobody waits for it to wake. When
#                                               others are watching: demos and study sessions.
# The workers need the NAT Gateway (terraform: nat_gateway = true, then apply). A GPU worker costs
# $1.86 an hour ($2.24 if only a 2xlarge is free). End every session with infra/stop_work.sh.
set -euo pipefail
source "$(dirname "$0")/aws_env.sh"   # AWS_PROFILE, AWS_REGION (M2a §4i)
ASG=neurolens-workers
HOLD_END=neurolens-warm-hold-end
ALARMS="neurolens-worker-idle neurolens-worker-scale-out neurolens-worker-scale-in"
BREAKER_RULE=neurolens-breaker-every-5-min
DB=neurolens-db

usage() { echo "usage: $0 [--max 1|2] [--keep-worker | --keep-worker-and-db] [--hours 1-4]" >&2; exit 2; }
WORKER=0 DB_AWAKE=0 HOURS=3 HOURS_GIVEN=0 MAX=1
while [ $# -gt 0 ]; do
  case "$1" in
    --keep-worker) WORKER=1 ;;
    --keep-worker-and-db) WORKER=1 DB_AWAKE=1 ;;
    --max) [ $# -ge 2 ] || usage; MAX=$2; shift ;;
    --hours) [ $# -ge 2 ] || usage; HOURS=$2; HOURS_GIVEN=1; shift ;;
    *) usage ;;
  esac
  shift
done
[ "$WORKER" = 1 ] || [ "$HOURS_GIVEN" = 0 ] || { echo "--hours goes with --keep-worker or --keep-worker-and-db." >&2; usage; }
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
  echo "No worker group yet (it is created once worker_ami_id is set). Nothing was started." >&2
  exit 1
fi
read -r MIN _ DES <<<"$SIZES"
# The idle alarm stays in ALARM for 5-10 minutes after the breaker fires (until a 5-minute slice
# with no worker in service). Meanwhile the breaker's schedule would set max 0 again, silently
# undoing this start. A warm hold (minimum 1) is safe: the breaker leaves it alone.
if [ "$WORKER" = 0 ] && [ "$MIN" = 0 ] && [ "$(aws cloudwatch describe-alarms \
    --alarm-names neurolens-worker-idle --query 'MetricAlarms[0].StateValue' --output text)" = ALARM ]; then
  echo "The idle alarm is still in ALARM (the circuit breaker stopped the workers): the breaker would" >&2
  echo "undo this start. Try again in about 10 minutes, or start a warm hold with --keep-worker. Nothing was started." >&2
  exit 1
fi
# Never below the workers the group already has: a lower max makes AWS end one, even mid-job.
if [ "$DES" -gt "$MAX" ]; then
  echo "The worker group has $DES workers; max $MAX would end one, possibly mid-job. Wait until the" >&2
  echo "queue is empty (scale-in ends them), or end the session with infra/stop_work.sh. Nothing was started." >&2
  exit 1
fi
# The workers' only way out to the internet (SQS, the Data API, HuggingFace) is the NAT Gateway
# (M3b §5). Without it a worker starts and then only waits for the idle alarm, billing meanwhile.
NAT=$(aws ec2 describe-nat-gateways --filter Name=tag:Project,Values=neurolens Name=state,Values=available \
  --query 'NatGateways[0].NatGatewayId' --output text)
if [ -z "$NAT" ] || [ "$NAT" = None ]; then
  echo "No NAT Gateway is available: the workers could not reach the internet. Set nat_gateway = true" >&2
  echo "in infra/terraform/terraform.tfvars and run terraform apply (about 2 minutes, then ~\$1.20 a day" >&2
  echo "until you set it back). Nothing was started." >&2
  exit 1
fi
# ...and the private subnets must actually route through it (a half-finished apply can create the
# gateway but not the route).
ROUTED=$(aws ec2 describe-route-tables --filters Name=tag:Name,Values=neurolens-private \
  Name=route.nat-gateway-id,Values="$NAT" --query 'RouteTables[0].RouteTableId' --output text)
if [ -z "$ROUTED" ] || [ "$ROUTED" = None ]; then
  echo "NAT Gateway $NAT exists but the private route table does not send traffic through it:" >&2
  echo "run terraform apply again. Nothing was started." >&2
  exit 1
fi
echo "NAT Gateway $NAT available and routed."

# From the first change on, a failed step leaves things part-started: say what to run.
trap 'echo "A step failed after the session was part-started (above): run infra/stop_work.sh to put everything back." >&2' ERR

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

if [ "$DB_AWAKE" = 1 ]; then
  # Terraform ignores min_capacity, so this does not fight it. stop_work.sh puts it back to 0.
  if ! aws rds modify-db-cluster --db-cluster-identifier "$DB" --apply-immediately \
      --serverless-v2-scaling-configuration MinCapacity=0.5,MaxCapacity=2 --output text >/dev/null; then
    echo "Could not keep Aurora awake (above). Nothing was started; end with infra/stop_work.sh if needed." >&2
    exit 1
  fi
  echo "Aurora kept awake (minimum 0.5 ACU, about 6 cents an hour) until infra/stop_work.sh."
fi

if [ "$WORKER" = 1 ]; then
  aws autoscaling update-auto-scaling-group --auto-scaling-group-name "$ASG" \
    --min-size 1 --max-size "$MAX" --desired-capacity "$((DES > 1 ? DES : 1))"
  echo "Warm hold until $END UTC: one worker is starting or running (billed from now) and stays even with no jobs."
  echo "After that, AWS ends it once the queue has been empty for 15 minutes. Run again to extend."
  [ "$DB_AWAKE" = 0 ] || echo "The hold's end does not release Aurora: only infra/stop_work.sh does (or the 6-hour database email)."
else
  # Never touches the minimum or the hold's end, so it cannot end a running hold.
  aws autoscaling update-auto-scaling-group --auto-scaling-group-name "$ASG" --max-size "$MAX"
  echo "Worker group: max $MAX. An upload starts a worker by itself (about 6.5 minutes on GPU)."
fi
