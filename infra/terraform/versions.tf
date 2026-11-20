terraform {
  # use_lockfile (S3-native state locking) needs Terraform 1.10 or newer.
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.28"
    }
    # SageMaker instance pools cannot be expressed by the aws provider; the endpoint
    # configuration alone uses Cloud Control.
    awscc = {
      source  = "hashicorp/awscc"
      version = "~> 1.107"
    }
    # Zips the Lambdas' code (the one zip).
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

# The region is one setting, and it is us-east-1: the hand-applied IAM policies, the role boundary
# and the worker image's pinned base image name it explicitly, and the state bucket above stays
# there. Nearer regions such as Singapore do not offer g6e.
provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project = "neurolens"
    }
  }
}

# Cloud Control manages the endpoint configuration only. It uses the
# same credentials and region as the aws provider.
provider "awscc" {
  region = var.region
}
