variable "bucket_name" {
  description = "Name of the S3 bucket for uploads, results and model data. Must be globally unique."
  type        = string
}

variable "queue_name" {
  description = "Name of the SQS queue that receives one message per uploaded video."
  type        = string
  default     = "neurolens-jobs"
}

variable "allowed_origins" {
  description = <<-EOT
    Web addresses allowed to upload straight to the bucket from a browser (CORS). An origin
    must match exactly, so localhost and 127.0.0.1 are listed separately. The CloudFront address
    is always added by Terraform; list only other origins here.
  EOT
  type        = list(string)
  default     = ["http://localhost:5003", "http://127.0.0.1:5003"]
}

variable "region" {
  description = "AWS region for everything except the Terraform state bucket (M2a §4i)."
  type        = string
  default     = "us-east-1"
}

variable "zones" {
  description = <<-EOT
    Availability zones for Aurora's private subnets. Aurora's subnet group needs at least two.
    The database is reached only through the Data API, so no zone needs a route to the internet.
  EOT
  type        = list(string)

  validation {
    condition     = length(var.zones) >= 2 && alltrue([for z in var.zones : startswith(z, var.region)])
    error_message = "zones must name at least two zones and all be in var.region (set them in terraform.tfvars)."
  }
}

variable "alert_email" {
  description = "Where the GPU and Lambda alarms are emailed. Set in terraform.tfvars."
  type        = string
}

variable "worker_image" {
  description = <<-EOT
    The digest of the image infra/build_image.sh built and pushed to ECR, e.g.
    "sha256:0123...". Empty until the first build; no endpoint exists then. A new digest creates a
    new SageMaker model and endpoint configuration and swaps the endpoint to them.
  EOT
  type        = string
  default     = ""

  validation {
    condition     = var.worker_image == "" || can(regex("^(sha256:)?[0-9a-f]{64}$", var.worker_image))
    error_message = "worker_image must be empty or a sha256 digest (optionally prefixed with sha256:)."
  }
}

variable "fake_inference" {
  description = "True only for the CPU rehearsal: one ml.m5.xlarge pool and FAKE_INFERENCE=1."
  type        = bool
  default     = false
}

variable "max_instances" {
  description = "Most GPUs the endpoint may run. Stays 1; the account's GPU quota is 2 (M3e §1)."
  type        = number
  default     = 1

  validation {
    condition     = var.max_instances >= 1 && var.max_instances <= 2
    error_message = "max_instances must be 1 (the normal state) or 2 (only if the quota allows it)."
  }
}

variable "auth_domain_prefix" {
  description = <<-EOT
    The Cognito sign-in page's address prefix (M3b §2a): <prefix>.auth.<region>.amazoncognito.com.
    Unique in the region; lowercase letters, numbers and hyphens; no "aws", "amazon" or
    "cognito". Google's authorized redirect URI is built from it (output google_redirect_uri).
  EOT
  type        = string
}

variable "google_client_id" {
  description = "The Google OAuth client's ID (not secret). Set in terraform.tfvars."
  type        = string
}
