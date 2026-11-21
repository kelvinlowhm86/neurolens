#!/usr/bin/env bash
# Guarded teardown. It deletes data with no undo, so every gate must pass before the next step:
# the caller and Terraform state must be the same account, the typed confirmation must match, the
# GPU stop must be proven, and the destroy must succeed before the hand-made secrets are deleted.
# Then it checks for leftovers; it never deletes the state bucket or the deploy user (the account
# owner does those by hand, docs/operating.md "Teardown").
# A rerun after a successful destroy resumes at step 3: Terraform's state is then empty, so there
# is nothing to stop or destroy, but the secrets and leftover check may still have work.
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
  echo "Nothing was deleted." >&2
  exit 1
fi

# Read the state before touching anything. An empty state means the stack is already destroyed,
# so a rerun can finish steps 4-5. A state that cannot be read is not the same thing: refuse
# rather than guess whether there is still a stack to destroy.
if ! STATE=$(terraform -chdir="$TF_DIR" state list 2>/dev/null); then
  echo "Refusing: cannot read Terraform state in $TF_DIR (check the backend and AWS_PROFILE)." >&2
  echo "Run: terraform -chdir=$TF_DIR init -backend-config=backend.hcl" >&2
  echo "Nothing was deleted." >&2
  exit 1
fi

if [ -n "$STATE" ]; then
  # State older than the code can already be missing settings such as force_destroy, so destroy
  # would delete most of the stack and then fail half-done. Plan against the current code first.
  PLAN_RC=0
  terraform -chdir="$TF_DIR" plan -detailed-exitcode -input=false -lock=false >/dev/null 2>&1 \
    || PLAN_RC=$?
  case "$PLAN_RC" in
    0) ;;
    2)
      echo "Refusing: Terraform's state is behind the code (the plan shows changes)." >&2
      echo "Apply the current code first: terraform -chdir=infra/terraform apply" >&2
      echo "Then rerun infra/teardown.sh. Nothing was deleted." >&2
      exit 1 ;;
    *)
      echo "Refusing: cannot plan with the current Terraform state and code. Nothing was deleted." >&2
      echo "Check it with: terraform -chdir=infra/terraform plan" >&2
      exit 1 ;;
  esac
  source "$INFRA/aws_env.sh"
  CALLER_ACCOUNT=$AWS_ACCOUNT_ID
  if ! TF_ACCOUNT=$(terraform -chdir="$TF_DIR" output -raw account_id 2>/dev/null) \
     || [ -z "$TF_ACCOUNT" ]; then
    echo "Refusing: Terraform has no account_id output in $AWS_REGION. Nothing was deleted." >&2
    exit 1
  fi
  if [ "$TF_ACCOUNT" != "$CALLER_ACCOUNT" ]; then
    echo "Refusing: the AWS caller is account $CALLER_ACCOUNT but Terraform's state belongs to" >&2
    echo "$TF_ACCOUNT. Check AWS_PROFILE before tearing anything down. Nothing was deleted." >&2
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
      echo 'Add region = "<aws-region>" to terraform.tfvars. Nothing was deleted.' >&2
      exit 1 ;;
  esac
  export AWS_REGION
  RESUME=1
  echo "Terraform's state is empty: the stack is already destroyed. This run only deletes the"
  echo "four hand-made Parameter Store secrets and the SageMaker history records, then checks"
  echo "for leftovers."
fi

# ---- Leftover checks ------------------------------------------------------------------------
# The same reads run twice: first as a probe before the confirmation, so a missing permission
# stops the teardown before anything is deleted; then for real after the destroy.
LEFTOVERS=""
LEFT_PROBLEMS=0
FAILED_CHECKS=""
TAGGED_LEFTOVERS=0
PROBE=0
leftover() {
  if [ "$PROBE" = 1 ]; then
    return 0
  fi
  LEFTOVERS="$LEFTOVERS  - $*"$'\n'
  echo "LEFTOVER: $*" >&2
}
check_failed() {
  echo "PROBLEM: could not finish the $1 check." >&2
  LEFT_PROBLEMS=1
  FAILED_CHECKS="$FAILED_CHECKS $1"
}
names_from() {
  printf '%s\n' "$1" | tr '\t' '\n' | sed '/^[[:space:]]*$/d'
}
check_leftovers() {
  if ! OUT=$(aws resourcegroupstaggingapi get-resources \
      --tag-filters Key=Project,Values=neurolens \
      --query 'ResourceTagMappingList[].ResourceARN' --output text); then
    check_failed "tagged-resource"
  else
    while IFS= read -r arn; do
      if [ "$PROBE" = 0 ]; then
        TAGGED_LEFTOVERS=1
      fi
      leftover "tagged AWS resource $arn"
    done < <(names_from "$OUT")
  fi

  # Secrets Manager is account-wide and holds other teams' secrets. The only secrets this stack
  # creates are RDS-managed ones named rds!cluster-*; list-secrets cannot reliably tie one to
  # this cluster by tag or description, so match that prefix and leave every other secret alone.
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
}

PROBE=1
check_leftovers
PROBE=0
if [ "$LEFT_PROBLEMS" = 1 ]; then
  echo "Refusing: cannot read the leftover checks:$FAILED_CHECKS. Nothing was deleted." >&2
  echo "The account owner may need to re-paste the current infra/iam policies (see" >&2
  echo "infra/iam/ACCOUNT_OWNER.md)." >&2
  exit 1
fi
LEFT_PROBLEMS=0
FAILED_CHECKS=""

if [ "$RESUME" = 0 ]; then
  echo "This destroys the NeuroLens stack in account $CALLER_ACCOUNT, region $AWS_REGION."
  echo "Bucket: $BUCKET. Everything in it is deleted."
  printf 'Type the AWS account ID (%s) to confirm destruction: ' "$CALLER_ACCOUNT"
else
  printf 'Type the AWS account ID (%s) to confirm: ' "$CALLER_ACCOUNT"
fi
ANSWER=""
read -r ANSWER || true
if [ "$ANSWER" != "$CALLER_ACCOUNT" ]; then
  echo "Confirmation did not match. Nothing was deleted." >&2
  exit 1
fi

if [ "$RESUME" = 0 ]; then
  # ---- 1. Stop the GPU and prove it ----------------------------------------------------------
  echo "1/4 Stopping the GPU..."
  ENDPOINT=$(terraform -chdir="$TF_DIR" output -raw gpu_endpoint) || {
    echo "Teardown stopped: cannot read gpu_endpoint from Terraform. Nothing was deleted." >&2
    exit 1
  }
  if ! DESCRIBE_ERR=$(aws sagemaker describe-endpoint --endpoint-name "$ENDPOINT" 2>&1 >/dev/null); then
    case "$DESCRIBE_ERR" in
      *ResourceNotFound*|*Could\ not\ find\ endpoint*)
        echo "No SageMaker endpoint $ENDPOINT exists: there is no GPU to stop." ;;
      *)
        echo "Teardown stopped: cannot check the SageMaker endpoint $ENDPOINT: $DESCRIBE_ERR" >&2
        echo "Nothing was deleted." >&2
        exit 1 ;;
    esac
  elif ! "$INFRA/stop_work.sh"; then
    echo "Teardown stopped: the GPU stop was not proven. Nothing was deleted." >&2
    exit 1
  fi

  # ---- 2. Destroy the stack -----------------------------------------------------------------
  echo "2/4 Destroying the Terraform stack..."
  if ! terraform -chdir="$TF_DIR" destroy -auto-approve -input=false; then
    echo "Teardown stopped: terraform destroy failed. The hand-made secrets were left in place." >&2
    exit 1
  fi
else
  echo "Skipping steps 1-2: the stack is already destroyed."
fi

# ---- 3. Delete the secrets and SageMaker history people cannot destroy --------------------
echo "3/4 Deleting the hand-made Parameter Store secrets and SageMaker history records..."
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

# SageMaker leaves lineage actions and contexts tagged Project=neurolens after the endpoint is
# gone. They are linked by associations, which must be deleted before the records themselves.
# The tagged list is mixed, so pick the action and context ARNs out of it below.
LINEAGE_PROBLEMS=0
if ! LINEAGE=$(aws resourcegroupstaggingapi get-resources \
    --tag-filters Key=Project,Values=neurolens \
    --query 'ResourceTagMappingList[].ResourceARN' --output text 2>&1); then
  echo "PROBLEM: could not list the SageMaker history records: $LINEAGE" >&2
  LINEAGE_PROBLEMS=1
  LINEAGE=""
fi
ACTIONS=""
CONTEXTS=""
while IFS= read -r arn; do
  case "$arn" in
    *:action/*) ACTIONS="$ACTIONS$arn"$'\n' ;;
    *:context/*) CONTEXTS="$CONTEXTS$arn"$'\n' ;;
  esac
done < <(names_from "$LINEAGE")

ASSOCIATIONS=""
while IFS= read -r arn; do
  if [ -z "$arn" ]; then
    continue
  fi
  for side in --source-arn --destination-arn; do
    if ! OUT=$(aws sagemaker list-associations "$side" "$arn" \
        --query 'AssociationSummaries[].[SourceArn,DestinationArn]' --output text 2>&1); then
      echo "PROBLEM: could not list associations for $arn: $OUT" >&2
      LINEAGE_PROBLEMS=1
      continue
    fi
    while IFS=$'\t' read -r src dst; do
      if [ -z "$src" ] || [ -z "$dst" ]; then
        continue
      fi
      case $'\n'"$ASSOCIATIONS" in
        *$'\n'"$src $dst"$'\n'*) continue ;;
      esac
      ASSOCIATIONS="$ASSOCIATIONS$src $dst"$'\n'
    done <<< "$OUT"
  done
done <<< "$ACTIONS$CONTEXTS"

while read -r src dst; do
  if [ -z "$src" ]; then
    continue
  fi
  if ! ERR=$(aws sagemaker delete-association --source-arn "$src" --destination-arn "$dst" \
      2>&1 >/dev/null); then
    case "$ERR" in
      *ResourceNotFound*) ;;
      *)
        echo "PROBLEM: could not delete the association $src -> $dst: $ERR" >&2
        LINEAGE_PROBLEMS=1
        ;;
    esac
  fi
done <<< "$ASSOCIATIONS"

while IFS= read -r arn; do
  if [ -z "$arn" ]; then
    continue
  fi
  if ! ERR=$(aws sagemaker delete-action --action-name "${arn##*/}" 2>&1 >/dev/null); then
    case "$ERR" in
      *ResourceNotFound*) ;;
      *)
        echo "PROBLEM: could not delete the SageMaker action $arn: $ERR" >&2
        LINEAGE_PROBLEMS=1
        ;;
    esac
  fi
done <<< "$ACTIONS"

while IFS= read -r arn; do
  if [ -z "$arn" ]; then
    continue
  fi
  if ! ERR=$(aws sagemaker delete-context --context-name "${arn##*/}" 2>&1 >/dev/null); then
    case "$ERR" in
      *ResourceNotFound*) ;;
      *)
        echo "PROBLEM: could not delete the SageMaker context $arn: $ERR" >&2
        LINEAGE_PROBLEMS=1
        ;;
    esac
  fi
done <<< "$CONTEXTS"

# ---- 4. Prove nothing under our name is left ----------------------------------------------
echo "4/4 Checking for leftovers..."
PROBE=0
check_leftovers

if [ -n "$LEFTOVERS" ] || [ "$SECRET_PROBLEMS" = 1 ] || [ "$LINEAGE_PROBLEMS" = 1 ] \
   || [ "$LEFT_PROBLEMS" = 1 ]; then
  printf 'TEARDOWN INCOMPLETE:\n%s' "$LEFTOVERS" >&2
  if [ "$TAGGED_LEFTOVERS" = 1 ]; then
    echo "AWS's tag list can lag after deletions; rerun infra/teardown.sh in a few minutes." >&2
    echo "A leftover that persists is real." >&2
  fi
  echo "Fix the items above, then run infra/teardown.sh again." >&2
  exit 1
fi

echo "TEARDOWN COMPLETE in $AWS_REGION for account $CALLER_ACCOUNT."
echo "Still to do by hand (docs/operating.md): outside-AWS accounts, the deploy user and policies,"
echo "the state bucket, and each laptop's local files."
