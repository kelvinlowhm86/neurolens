#!/usr/bin/env bash
# Starts processing (docs/M3e_spec.md §6b): the SageMaker endpoint's normal state is max 1.
#   infra/start_work.sh                         scale out on demand; the GPU stops after 7 idle minutes
#   infra/start_work.sh --keep-warm [--hours N] one GPU now, for N hours (1-4, default 3); AWS ends it
#   infra/start_work.sh --keep-db               keep Aurora awake (minimum 0.5 ACU)
set -euo pipefail

VARIANT_MAIN=main
KEEP_WARM_END=neurolens-keep-warm-end
ENDPOINT=neurolens
DB=neurolens-db
HOURS=3
KEEP_WARM=0
KEEP_DB=0
HOURS_GIVEN=0

usage() {
  echo "usage: $0 [--keep-warm [--hours 1-4]] [--keep-db]" >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --keep-warm) KEEP_WARM=1 ;;
    --keep-db) KEEP_DB=1 ;;
    --hours) [ $# -ge 2 ] || usage; HOURS=$2; HOURS_GIVEN=1; shift ;;
    *) usage ;;
  esac
  shift
done

[ "$KEEP_WARM" = 1 ] || [ "$HOURS_GIVEN" = 0 ] || {
  echo "--hours goes with --keep-warm." >&2
  usage
}
case "$HOURS" in 1|2|3|4) ;; *) echo "--hours must be 1, 2, 3 or 4 (got $HOURS)." >&2; exit 2 ;; esac

source "$(dirname "$0")/aws_env.sh"
RESOURCE="endpoint/$ENDPOINT/variant/$VARIANT_MAIN"
DIMENSION=sagemaker:variant:DesiredInstanceCount

if [ "$KEEP_WARM" = 1 ]; then
  END=$(date -u -v+"${HOURS}"H +%Y-%m-%dT%H:%M:%S 2>/dev/null \
        || date -u -d "+${HOURS} hours" +%Y-%m-%dT%H:%M:%S)
  aws application-autoscaling put-scheduled-action \
    --service-namespace sagemaker \
    --scheduled-action-name "$KEEP_WARM_END" \
    --resource-id "$RESOURCE" \
    --scalable-dimension "$DIMENSION" \
    --schedule "at($END)" \
    --scalable-target-action "MinCapacity=0,MaxCapacity=1"
  aws application-autoscaling register-scalable-target \
    --service-namespace sagemaker \
    --resource-id "$RESOURCE" \
    --scalable-dimension "$DIMENSION" \
    --min-capacity 1 \
    --max-capacity 1
  echo "GPU kept warm until $END UTC (min 1), then AWS ends it after 7 idle minutes."
else
  aws application-autoscaling register-scalable-target \
    --service-namespace sagemaker \
    --resource-id "$RESOURCE" \
    --scalable-dimension "$DIMENSION" \
    --min-capacity 0 \
    --max-capacity 1
  echo "GPU endpoint $ENDPOINT: max 1. An upload starts a GPU by itself."
fi

if [ "$KEEP_DB" = 1 ]; then
  aws rds modify-db-cluster --db-cluster-identifier "$DB" --apply-immediately \
    --serverless-v2-scaling-configuration MinCapacity=0.5 --output text >/dev/null
  echo "Aurora kept awake (minimum 0.5 ACU) until infra/stop_work.sh."
fi
