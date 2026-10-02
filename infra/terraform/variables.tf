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
    must match exactly, so localhost and 127.0.0.1 are listed separately. To add M3's HTTPS
    address later, add it here and apply.
  EOT
  type        = list(string)
  default     = ["http://localhost:5003", "http://127.0.0.1:5003"]
}

variable "zones" {
  description = <<-EOT
    Availability zones that offer g6e.xlarge (check with `aws ec2 describe-instance-type-offerings
    --location-type availability-zone --filters Name=instance-type,Values=g6e.xlarge`), one private
    worker subnet each: more zones give the worker group more places to find a GPU. The first also
    holds the public subnet (NAT instance).
  EOT
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c", "us-east-1d"]
}

variable "build_extra_zones" {
  description = "Further zones offering g6e.xlarge, each with a public subnet the image build can fall back to when the first zone has no capacity."
  type        = list(string)
  default     = ["us-east-1b", "us-east-1c", "us-east-1d"]
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

variable "worker_instance_type" {
  description = "g6e.xlarge, or g6e.2xlarge if the build's peak-RAM rule says so; t3.large for the wiring rehearsal."
  type        = string
  default     = "g6e.xlarge"
}

variable "worker_fake_inference" {
  description = "True only for the wiring rehearsal on a CPU instance: FAKE_INFERENCE=1 in env.conf."
  type        = bool
  default     = false
}
