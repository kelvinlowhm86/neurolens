#!/usr/bin/env bash
# Emergency pause (docs/M3e_spec.md §6b): no GPU may start or stay on, and no keep-warm survives.
# It deletes the keep-warm action before scaling down, then reads the target back.
# Prints ALL STOPPED only when every step succeeded; otherwise NOT CONFIRMED and exit 1.
set -uo pipefail

VARIANT_MAIN=main
KEEP_WARM_END=neurolens-keep-warm-end
ENDPOINT=neurolens
PROBLEMS=""
problem() {
  PROBLEMS="$PROBLEMS  - $*"$'\n'
  echo "PROBLEM: $*" >&2
}

source "$(dirname "$0")/aws_env.sh"
RESOURCE="endpoint/$ENDPOINT/variant/$VARIANT_MAIN"
DIMENSION=sagemaker:variant:DesiredInstanceCount

# The keep-warm end timer is a fixed name. Delete it first, even when none is listed: a stale
# timer in a later session must not raise the minimum behind our back. Nothing to delete is the
# normal case.
if ! DELETE_ERR=$(aws application-autoscaling delete-scheduled-action \
    --service-namespace sagemaker \
    --scheduled-action-name "$KEEP_WARM_END" \
    --resource-id "$RESOURCE" \
    --scalable-dimension "$DIMENSION" 2>&1 1>/dev/null); then
  case "$DELETE_ERR" in
    *ObjectNotFoundException*) ;;
    *) problem "could not delete the keep-warm action ($KEEP_WARM_END): $DELETE_ERR" ;;
  esac
fi

aws application-autoscaling register-scalable-target \
  --service-namespace sagemaker \
  --resource-id "$RESOURCE" \
  --scalable-dimension "$DIMENSION" \
  --min-capacity 0 \
  --max-capacity 0 \
  || problem "could not set the scalable target to 0/0"

if ! READBACK=$(aws application-autoscaling describe-scalable-targets \
    --service-namespace sagemaker \
    --resource-ids "$RESOURCE" \
    --scalable-dimension "$DIMENSION" \
    --query 'ScalableTargets[0].[MinCapacity,MaxCapacity]' \
    --output text 2>&1); then
  problem "could not read the scalable target back: $READBACK"
else
  read -r MIN MAX <<< "$READBACK" || true
  if [ "${MIN:-}" != "0" ] || [ "${MAX:-}" != "0" ]; then
    problem "the scalable target still reads min=${MIN:-?} max=${MAX:-?}; it must be 0/0"
  fi
fi

aws sagemaker update-endpoint-weights-and-capacities \
  --endpoint-name "$ENDPOINT" \
  --desired-weights-and-capacities "VariantName=$VARIANT_MAIN,DesiredInstanceCount=0" \
  || problem "could not set the endpoint's desired instance count to 0"

STOPPED=0
for _ in $(seq 1 60); do
  if ! COUNT=$(aws sagemaker describe-endpoint --endpoint-name "$ENDPOINT" \
      --query 'ProductionVariants[0].CurrentInstanceCount' --output text); then
    problem "could not read the endpoint's instance count"
    break
  fi
  if [ "$COUNT" = "0" ] || [ "$COUNT" = "None" ]; then
    STOPPED=1
    break
  fi
  sleep 10
done
[ "$STOPPED" = 1 ] || problem "the endpoint still shows $COUNT instance(s); it did not reach 0"

if [ -n "$PROBLEMS" ]; then
  printf 'NOT CONFIRMED in %s:\n%s' "${AWS_REGION:-unknown region}" "$PROBLEMS" >&2
  exit 1
fi
echo "ALL STOPPED in ${AWS_REGION:-unknown region}: scalable target 0/0, no keep-warm, endpoint at 0."
