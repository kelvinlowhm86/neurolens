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
