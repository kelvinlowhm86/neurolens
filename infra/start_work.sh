#!/usr/bin/env bash
# Starts processing. The endpoint's maximum comes from Terraform's max_instances output; the
# stop time in the messages comes from Terraform's idle_stop_minutes output.
#   infra/start_work.sh                         scale out on demand; the GPU stops when idle
#   infra/start_work.sh --keep-warm [--hours N] one GPU now, for N hours; AWS ends it
set -euo pipefail

VARIANT_MAIN=main
KEEP_WARM_END=neurolens-keep-warm-end
ENDPOINT=neurolens
DEFAULT_HOURS=3
MIN_HOURS=1
MAX_HOURS=5
HOURS=$DEFAULT_HOURS
KEEP_WARM=0
HOURS_GIVEN=0

usage() {
  echo "usage: $0 [--keep-warm [--hours $MIN_HOURS-$MAX_HOURS]]" >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --keep-warm) KEEP_WARM=1 ;;
    --hours) [ $# -ge 2 ] || usage; HOURS=$2; HOURS_GIVEN=1; shift ;;
    *) usage ;;
  esac
  shift
done

[ "$KEEP_WARM" = 1 ] || [ "$HOURS_GIVEN" = 0 ] || {
  echo "--hours goes with --keep-warm." >&2
  usage
}
if ! [[ "$HOURS" =~ ^[0-9]+$ ]] || [ "$HOURS" -lt "$MIN_HOURS" ] || [ "$HOURS" -gt "$MAX_HOURS" ]; then
  echo "--hours must be $MIN_HOURS to $MAX_HOURS (got $HOURS)." >&2
  exit 2
fi

source "$(dirname "$0")/aws_env.sh"
TERRAFORM_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)/infra/terraform"
MAX_INSTANCES=$(terraform -chdir="$TERRAFORM_DIR" output -raw max_instances) || {
  echo "Cannot read max_instances from Terraform. Is the deployment initialised?" >&2
  exit 1
}
IDLE_MINUTES=$(terraform -chdir="$TERRAFORM_DIR" output -raw idle_stop_minutes) || {
  echo "Cannot read idle_stop_minutes from Terraform. Is the deployment initialised?" >&2
  exit 1
}
if ! [[ "$MAX_INSTANCES" =~ ^[0-9]+$ ]] || [ "$MAX_INSTANCES" -lt 1 ]; then
  echo "Terraform's max_instances must be a whole number of at least 1 (got $MAX_INSTANCES)." >&2
  exit 1
fi
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
    --scalable-target-action "MinCapacity=0,MaxCapacity=$MAX_INSTANCES"
  aws application-autoscaling register-scalable-target \
    --service-namespace sagemaker \
    --resource-id "$RESOURCE" \
    --scalable-dimension "$DIMENSION" \
    --min-capacity 1 \
    --max-capacity "$MAX_INSTANCES"
  echo "GPU kept warm until $END UTC (min 1), then AWS ends it after $IDLE_MINUTES idle minutes."
else
  aws application-autoscaling register-scalable-target \
    --service-namespace sagemaker \
    --resource-id "$RESOURCE" \
    --scalable-dimension "$DIMENSION" \
    --min-capacity 0 \
    --max-capacity "$MAX_INSTANCES"
  echo "GPU endpoint $ENDPOINT: max $MAX_INSTANCES. An upload starts a GPU by itself."
fi
