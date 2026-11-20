#!/usr/bin/env bash
# Guarded teardown. It deletes data with no undo, so every gate must pass before the next step:
# the caller and Terraform state must be the same account, the typed confirmation must match, the
# GPU stop must be proven, the backup must succeed, and the destroy must succeed before the
# hand-made secrets are deleted. Then it checks for leftovers; it never deletes the state bucket
# or the deploy user (the account owner does those by hand, docs/operating.md "Teardown").
# A rerun after a successful destroy resumes at step 4: Terraform's state is then empty, so there
# is nothing to stop, back up or destroy, but the secrets and leftover check may still have work.
set -euo pipefail

ROOT=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)
INFRA="$ROOT/infra"
TF_DIR="$INFRA/terraform"
TFVARS="$TF_DIR/terraform.tfvars"

export AWS_PROFILE="${NEUROLENS_AWS_PROFILE:-neurolens}"
if ! CALLER_ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) \
   || [ -z "$CALLER_ACCOUNT" ]; then
  echo "Cannot read the AWS account for profile '$AWS_PROFILE'. Check that the profile exists and its credentials work:" >&2
  echo "  AWS_PROFILE=$AWS_PROFILE aws sts get-caller-identity" >&2
  exit 1
fi

# Read the state before touching anything. An empty state means the stack is already destroyed,
# so a rerun can finish steps 4-5. A state that cannot be read is not the same thing: refuse
# rather than guess whether there is still a stack to destroy.
if ! STATE=$(terraform -chdir="$TF_DIR" state list 2>/dev/null); then
  echo "Refusing: cannot read Terraform state in $TF_DIR (check the backend and AWS_PROFILE)." >&2
  echo "Run: terraform -chdir=$TF_DIR init -backend-config=backend.hcl" >&2
  exit 1
fi

if [ -n "$STATE" ]; then
  source "$INFRA/aws_env.sh"
  CALLER_ACCOUNT=$AWS_ACCOUNT_ID
  if ! TF_ACCOUNT=$(terraform -chdir="$TF_DIR" output -raw account_id 2>/dev/null) \
     || [ -z "$TF_ACCOUNT" ]; then
    echo "Refusing: Terraform has no account_id output in $AWS_REGION." >&2
    exit 1
  fi
  if [ "$TF_ACCOUNT" != "$CALLER_ACCOUNT" ]; then
    echo "Refusing: the AWS caller is account $CALLER_ACCOUNT but Terraform's state belongs to" >&2
    echo "$TF_ACCOUNT. Check AWS_PROFILE before tearing anything down." >&2
    exit 1
  fi
  BUCKET=$(terraform -chdir="$TF_DIR" output -raw bucket_name)
  RESUME=0
else
  # The stack is gone but the hand-made parameters and any leftovers may remain. Their region is
  # the deployment region, written in terraform.tfvars (the setting's one home).
  AWS_REGION=$(sed -n -E 's/^[[:space:]]*region[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$TFVARS" 2>/dev/null \
    | tail -n 1)
  case "$AWS_REGION" in
    [a-z][a-z]-*-[0-9]) ;;
    *)
      echo "Refusing: Terraform's state is empty but $TFVARS has no region to finish in." >&2
      echo 'Add region = "<aws-region>" to terraform.tfvars.' >&2
      exit 1 ;;
  esac
  export AWS_REGION
  RESUME=1
  echo "Terraform's state is empty: the stack is already destroyed. This run only deletes the"
  echo "four hand-made Parameter Store secrets and checks for leftovers."
fi

if [ "$RESUME" = 0 ]; then
  echo "This destroys the NeuroLens stack in account $CALLER_ACCOUNT, region $AWS_REGION."
  echo "Bucket: $BUCKET. The experiments/ prefix is backed up first; everything else is deleted."
  printf 'Type the AWS account ID (%s) to confirm destruction: ' "$CALLER_ACCOUNT"
else
  printf 'Type the AWS account ID (%s) to confirm: ' "$CALLER_ACCOUNT"
fi
ANSWER=""
read -r ANSWER || true
if [ "$ANSWER" != "$CALLER_ACCOUNT" ]; then
  echo "Confirmation did not match. Nothing was changed." >&2
  exit 1
fi

if [ "$RESUME" = 0 ]; then
  # ---- 1. Stop the GPU and prove it ----------------------------------------------------------
  echo "1/5 Stopping the GPU..."
  if ! "$INFRA/stop_work.sh"; then
    echo "Teardown stopped: the GPU stop was not proven. Nothing was deleted." >&2
    exit 1
  fi

  # ---- 2. Back up what the destroy would remove ---------------------------------------------
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
else
  echo "Skipping steps 1-3: the stack is already destroyed."
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

# Secrets Manager is account-wide and holds other teams' secrets. The only secrets this stack
# creates are RDS-managed ones named rds!cluster-*; list-secrets cannot reliably tie one to this
# cluster by tag or description, so match that prefix and leave every other secret alone.
if ! OUT=$(aws secretsmanager list-secrets \
    --query 'SecretList[].Name' --output text); then
  check_failed "Secrets Manager"
else
  while IFS= read -r name; do
    case "$name" in
      rds!cluster-*) leftover "Secrets Manager secret $name" ;;
    esac
  done < <(names_from "$OUT")
fi

if ! OUT=$(aws sagemaker list-endpoint-configs \
    --query 'EndpointConfigs[].EndpointConfigName' --output text); then
  check_failed "SageMaker endpoint-configuration"
else
  while IFS= read -r name; do
    case "$name" in
      neurolens*) leftover "SageMaker endpoint configuration $name" ;;
    esac
  done < <(names_from "$OUT")
fi

if ! OUT=$(aws sagemaker list-models \
    --query 'Models[].ModelName' --output text); then
  check_failed "SageMaker model"
else
  while IFS= read -r name; do
    case "$name" in
      neurolens*) leftover "SageMaker model $name" ;;
    esac
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
if [ "$RESUME" = 0 ]; then
  echo "Backup: $BACKUP_DIR"
else
  echo "No new backup was made: the stack was already destroyed."
fi
echo "Still to do by hand (docs/operating.md): outside-AWS accounts, the deploy user and policies,"
echo "the state bucket, and each laptop's local files."
