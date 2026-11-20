#!/usr/bin/env bash
# Guarded teardown. It deletes data with no undo, so every gate must pass before the next step:
# the caller and Terraform state must be the same account, the typed confirmation must match, the
# GPU stop must be proven, the backup must succeed, and the destroy must succeed before the
# hand-made secrets are deleted. Then it checks for leftovers; it never deletes the state bucket
# or the deploy user (the account owner does those by hand, docs/operating.md "Teardown").
set -euo pipefail

ROOT=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)
INFRA="$ROOT/infra"
TF_DIR="$INFRA/terraform"

source "$INFRA/aws_env.sh"
CALLER_ACCOUNT=$AWS_ACCOUNT_ID

if ! TF_ACCOUNT=$(terraform -chdir="$TF_DIR" output -raw account_id 2>/dev/null) \
   || [ -z "$TF_ACCOUNT" ]; then
  echo "Refusing: Terraform has no state in $AWS_REGION (or no account_id output)." >&2
  exit 1
fi
if [ "$TF_ACCOUNT" != "$CALLER_ACCOUNT" ]; then
  echo "Refusing: the AWS caller is account $CALLER_ACCOUNT but Terraform's state belongs to" >&2
  echo "$TF_ACCOUNT. Check AWS_PROFILE before tearing anything down." >&2
  exit 1
fi

BUCKET=$(terraform -chdir="$TF_DIR" output -raw bucket_name)

echo "This destroys the NeuroLens stack in account $CALLER_ACCOUNT, region $AWS_REGION."
echo "Bucket: $BUCKET. The experiments/ prefix is backed up first; everything else is deleted."
printf 'Type the AWS account ID (%s) to confirm destruction: ' "$CALLER_ACCOUNT"
ANSWER=""
read -r ANSWER || true
if [ "$ANSWER" != "$CALLER_ACCOUNT" ]; then
  echo "Confirmation did not match. Nothing was changed." >&2
  exit 1
fi

# ---- 1. Stop the GPU and prove it ----------------------------------------------------------
echo "1/5 Stopping the GPU..."
if ! "$INFRA/stop_work.sh"; then
  echo "Teardown stopped: the GPU stop was not proven. Nothing was deleted." >&2
  exit 1
fi

# ---- 2. Back up what the destroy would remove ----------------------------------------------
echo "2/5 Backing up experiments/..."
BACKUP_DIR="$ROOT/data/teardown_backup/$CALLER_ACCOUNT-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"
if ! aws s3 sync "s3://$BUCKET/experiments/" "$BACKUP_DIR/" --only-show-errors; then
  echo "Teardown stopped: the backup to $BACKUP_DIR failed. Nothing was destroyed." >&2
  exit 1
fi
echo "Backup kept at $BACKUP_DIR"

# ---- 3. Destroy the stack -----------------------------------------------------------------
echo "3/5 Destroying the Terraform stack..."
if ! terraform -chdir="$TF_DIR" destroy -auto-approve -input=false; then
  echo "Teardown stopped: terraform destroy failed. The hand-made secrets were left in place." >&2
  exit 1
fi

# ---- 4. Delete the secrets people created by hand -----------------------------------------
echo "4/5 Deleting the hand-made Parameter Store secrets..."
SSM_SECRETS=(
  /neurolens/terraform/google_client_secret
  /neurolens/web/flask_secret_key
  /neurolens/web/cognito_client_secret
  /neurolens/web/youtube_api_key
)
SECRET_PROBLEMS=0
for name in "${SSM_SECRETS[@]}"; do
  if ! ERR=$(aws ssm delete-parameter --name "$name" 2>&1 >/dev/null); then
    case "$ERR" in
      *ParameterNotFound*) ;;
      *)
        echo "PROBLEM: could not delete $name: $ERR" >&2
        SECRET_PROBLEMS=1
        ;;
    esac
  fi
done

# ---- 5. Prove nothing under our name is left ----------------------------------------------
echo "5/5 Checking for leftovers..."
LEFTOVERS=""
CHECK_PROBLEMS=0
leftover() {
  LEFTOVERS="$LEFTOVERS  - $*"$'\n'
  echo "LEFTOVER: $*" >&2
}
check_failed() {
  echo "PROBLEM: could not finish the $1 check." >&2
  CHECK_PROBLEMS=1
}
names_from() {
  printf '%s\n' "$1" | tr '\t' '\n' | sed '/^[[:space:]]*$/d'
}

if ! OUT=$(aws resourcegroupstaggingapi get-resources \
    --tag-filters Key=Project,Values=neurolens \
    --query 'ResourceTagMappingList[].ResourceARN' --output text); then
  check_failed "tagged-resource"
else
  while IFS= read -r arn; do
    leftover "tagged AWS resource $arn"
  done < <(names_from "$OUT")
fi

if ! OUT=$(aws secretsmanager list-secrets \
    --query 'SecretList[].Name' --output text); then
  check_failed "Secrets Manager"
else
  while IFS= read -r name; do
    leftover "Secrets Manager secret $name"
  done < <(names_from "$OUT")
fi

if ! OUT=$(aws sagemaker list-endpoint-configs \
    --query 'EndpointConfigs[].EndpointConfigName' --output text); then
  check_failed "SageMaker endpoint-configuration"
else
  while IFS= read -r name; do
    leftover "SageMaker endpoint configuration $name"
  done < <(names_from "$OUT")
fi

if ! OUT=$(aws sagemaker list-models \
    --query 'Models[].ModelName' --output text); then
  check_failed "SageMaker model"
else
  while IFS= read -r name; do
    leftover "SageMaker model $name"
  done < <(names_from "$OUT")
fi

if ! OUT=$(aws ssm get-parameters-by-path --path /neurolens/ --recursive \
    --query 'Parameters[].Name' --output text); then
  check_failed "Parameter Store"
else
  while IFS= read -r name; do
    leftover "Parameter Store parameter $name"
  done < <(names_from "$OUT")
fi

if ! OUT=$(aws iam list-roles --query 'Roles[].RoleName' --output text); then
  check_failed "IAM role"
else
  while IFS= read -r role; do
    case "$role" in
      neurolens*) leftover "IAM role $role" ;;
    esac
  done < <(names_from "$OUT")
fi

if [ -n "$LEFTOVERS" ] || [ "$SECRET_PROBLEMS" = 1 ] || [ "$CHECK_PROBLEMS" = 1 ]; then
  printf 'TEARDOWN INCOMPLETE:\n%s' "$LEFTOVERS" >&2
  echo "Fix the items above, then run infra/teardown.sh again." >&2
  exit 1
fi

echo "TEARDOWN COMPLETE in $AWS_REGION for account $CALLER_ACCOUNT."
echo "Backup: $BACKUP_DIR"
echo "Still to do by hand (docs/operating.md): outside-AWS accounts, the deploy user and policies,"
echo "the state bucket, and each laptop's local files."
