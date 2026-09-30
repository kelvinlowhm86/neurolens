terraform {
  # use_lockfile (S3-native state locking) needs Terraform 1.10 or newer.
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Terraform's record of what exists (its "state") lives in a small separate S3 bucket, not on
  # one laptop. The bucket name is passed at init time so it is not committed:
  #   terraform init -backend-config=backend.hcl      (see README.md)
  backend "s3" {
    key          = "m1/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
  }
}

# us-east-1 on purpose: the g6e GPU family used from M2a onward is not offered in Singapore.
provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      Project   = "neurolens"
      Milestone = "M1"
    }
  }
}
