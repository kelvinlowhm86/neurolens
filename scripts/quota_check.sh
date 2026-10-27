#!/usr/bin/env bash
# Can this account run the GPU workers? Checks g6e availability per AZ and the G/VT vCPU quotas.
#   AWS_PROFILE=neurolens scripts/quota_check.sh [region]
set -euo pipefail
REGION="${1:-ap-southeast-1}"
TYPE="g6e.2xlarge"   # 8 vCPUs per worker

echo "== $TYPE offered in $REGION =="
aws ec2 describe-instance-type-offerings --region "$REGION" --location-type availability-zone \
  --filters "Name=instance-type,Values=$TYPE" --query 'InstanceTypeOfferings[].Location' --output text \
  | tr '\t' '\n' | sed 's/^/  /' || true

for code in L-3819A6DF L-DB2E81BA; do
  name=$([[ $code == L-3819A6DF ]] && echo "G and VT Spot vCPUs" || echo "G and VT On-Demand vCPUs")
  value=$(aws service-quotas get-service-quota --region "$REGION" --service-code ec2 --quota-code "$code" \
            --query 'Quota.Value' --output text 2>/dev/null || echo "unknown")
  echo "== $name: $value  (2 workers need 16)"
done
