# Sourced by the scripts in infra/ (not run on its own). Sets the AWS profile and the region
# (M2a §4i): NEUROLENS_AWS_REGION if set, otherwise Terraform's `region` output, so the region is
# written down in one place (terraform.tfvars).
export AWS_PROFILE="${NEUROLENS_AWS_PROFILE:-neurolens}"
if [ -z "${NEUROLENS_AWS_REGION:-}" ]; then
  NEUROLENS_AWS_REGION=$(terraform -chdir="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)/infra/terraform" \
    output -raw region 2>/dev/null) || NEUROLENS_AWS_REGION=""
  case "$NEUROLENS_AWS_REGION" in
    [a-z][a-z]-*-[0-9]) ;;
    *) echo "Cannot read the region from Terraform (run terraform apply once; it adds the region output)." >&2
       echo "To go on anyway, set it by hand, e.g.: NEUROLENS_AWS_REGION=us-east-1 $0 ..." >&2
       exit 1 ;;
  esac
fi
export AWS_REGION="$NEUROLENS_AWS_REGION"
