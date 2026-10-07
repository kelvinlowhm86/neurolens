terraform {
  # use_lockfile (S3-native state locking) needs Terraform 1.10 or newer.
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.28"
    }
    # Zips the circuit breaker's code for Lambda (M2b §2c).
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }

  # Terraform's record of what exists (its "state") lives in a small separate S3 bucket, not on
  # one laptop. The bucket name is passed at init time so it is not committed:
  #   terraform init -backend-config=backend.hcl      (see README.md)
  backend "s3" {
    # One state for the whole project; the name stays so nothing is orphaned. A deployment in
    # another region would need its own key (-backend-config=key=<region>/terraform.tfstate).
    key          = "m1/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
  }
}

# The region is one setting (M2a §4i): us-east-1 by default, because the g6e GPU family used from
# M2a onward is not offered in Singapore. The state bucket above stays in us-east-1 whatever this is.
provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "neurolens"
      Milestone = "M2a"
    }
  }
}
