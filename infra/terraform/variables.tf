variable "bucket_name" {
  description = "Name of the S3 bucket for uploads, results and code. Must be globally unique."
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
    Availability zones that offer g6e.xlarge (check with `aws ec2 describe-instance-type-offerings
    --location-type availability-zone --filters Name=instance-type,Values=g6e.xlarge`), one private
    worker subnet each: more zones give the worker group more places to find a GPU. The first also
    holds the public subnet (NAT Gateway).
  EOT
  type        = list(string)

  validation {
    condition     = length(var.zones) > 0 && alltrue([for z in var.zones : startswith(z, var.region)])
    error_message = "zones must be non-empty and all in var.region (set them in terraform.tfvars)."
  }
}

variable "build_extra_zones" {
  description = "Further zones offering g6e.xlarge, each with a public subnet the image build can fall back to when the first zone has no capacity."
  type        = list(string)

  validation {
    condition     = alltrue([for z in var.build_extra_zones : startswith(z, var.region)])
    error_message = "build_extra_zones must all be in var.region (set them in terraform.tfvars)."
  }
}

variable "alert_email" {
  description = "Where the 3-hour GPU alarm is emailed. Set in terraform.tfvars (git-ignored)."
  type        = string
}

variable "worker_ami_id" {
  description = "The software-only image from infra/build_ami.sh. Empty: no Launch Template or Auto Scaling group yet."
  type        = string
  default     = ""
}

variable "worker_instance_types" {
  description = "On-demand worker types in order of preference: the next is tried when the one before is sold out in every zone. [\"t3.large\"] for the wiring rehearsal."
  type        = list(string)
  default     = ["g6e.xlarge", "g6e.2xlarge"]
}

variable "scale_out_warmup_seconds" {
  description = <<-EOT
    How long a newly launched worker counts as "still starting" for the scale-out policy (M2b §2a).
    A GPU boot takes about 6.5 minutes and the SQS metric lags 1-3 minutes, so 900 s keeps one
    waiting job from launching a second worker while the first boots.
  EOT
  type        = number
  default     = 900
}

variable "worker_fake_job_seconds" {
  description = "CPU rehearsal only (with worker_fake_inference): each fake predict call sleeps this long, so a job is long enough to interrupt (FAKE_INFERENCE_SECONDS, M2b §1a). 0 = off."
  type        = number
  default     = 0
}

variable "worker_fake_inference" {
  description = "True only for the wiring rehearsal on a CPU instance: FAKE_INFERENCE=1 in env.conf."
  type        = bool
  default     = false
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

variable "nat_gateway" {
  description = <<-EOT
    The workers' way out to the internet (SQS, the Data API, HuggingFace), M3b §5. true on days with
    GPU work and for the deployed window (about $1.20 a day while it exists), false otherwise. The
    website never needs it. Terraform refuses false while the worker group may start workers or one is running: run
    infra/stop_work.sh first.
  EOT
  type        = bool
  default     = false
}
