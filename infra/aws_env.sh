# Sourced by the scripts in infra/ (not run on its own). Sets the AWS profile and the region
# (M2a §4i) from Terraform's `region` output only, so the region is written down in one place
# (terraform.tfvars). Terraform, not a copy: it records where the resources actually are. The
# shell's NEUROLENS_AWS_REGION (the Python app's copy, from .env) is ignored on purpose: a stale
# copy would make stop_work.sh check the wrong region. No region is guessed when Terraform cannot
# answer.
export AWS_PROFILE="${NEUROLENS_AWS_PROFILE:-neurolens}"
NEUROLENS_AWS_REGION=$(terraform -chdir="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)/infra/terraform" \
  output -raw region 2>/dev/null) || NEUROLENS_AWS_REGION=""
case "$NEUROLENS_AWS_REGION" in
  [a-z][a-z]-*-[0-9]) ;;
  *) echo "Cannot read the region from Terraform. Check that Terraform is set up and the AWS profile works:" >&2
     echo "  AWS_PROFILE=$AWS_PROFILE terraform -chdir=infra/terraform init -backend-config=backend.hcl" >&2
     echo "  AWS_PROFILE=$AWS_PROFILE terraform -chdir=infra/terraform output region" >&2
     exit 1 ;;
esac
export AWS_REGION="$NEUROLENS_AWS_REGION"
