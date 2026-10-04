data "aws_caller_identity" "current" {}

# ─── S3 bucket ─────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "main" {
  bucket = var.bucket_name
}

# Nothing in this bucket is ever public. Uploads use signed, time-limited forms.
resource "aws_s3_bucket_public_access_block" "main" {
  bucket                  = aws_s3_bucket.main.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Disables ACLs entirely (the presigned-POST upload flow neither uses nor needs them).
resource "aws_s3_bucket_ownership_controls" "main" {
  bucket = aws_s3_bucket.main.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Encrypt everything at rest (SSE-S3; SSE-KMS is a possible upgrade, not needed here).
resource "aws_s3_bucket_server_side_encryption_configuration" "main" {
  bucket = aws_s3_bucket.main.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Refuse any request that is not over HTTPS, including the presigned POST and any GET.
resource "aws_s3_bucket_policy" "main" {
  bucket = aws_s3_bucket.main.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.main.arn,
        "${aws_s3_bucket.main.arn}/*",
      ]
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
    }]
  })

  # The public access block must exist before a policy is attached.
  depends_on = [aws_s3_bucket_public_access_block.main]
}

# Prefix-scoped expiry, not one blanket rule. Days are the smallest unit S3 lifecycle uses.
#   uploads/ and status/  2 days (S3 rounds to midnight UTC: 2-3 days)   transient per-job files (the input videos are the big ones)
#   results/              30 d   how long a result stays viewable / downloadable (product decision)
#   code/ and experiments/ never expire: M2a's boot pulls code/latest.zip on every GPU launch
#   (a 404 would stop the worker coming up) and M4 reads experiments/* for the final report.
resource "aws_s3_bucket_lifecycle_configuration" "main" {
  bucket = aws_s3_bucket.main.id

  rule {
    id     = "expire-uploads"
    status = "Enabled"
    filter {
      prefix = "uploads/"
    }
    expiration {
      days = 2
    }
  }

  rule {
    id     = "expire-status"
    status = "Enabled"
    filter {
      prefix = "status/"
    }
    expiration {
      days = 2
    }
  }

  rule {
    id     = "expire-results"
    status = "Enabled"
    filter {
      prefix = "results/"
    }
    expiration {
      days = 30
    }
  }
}

# Lets the browser POST a file straight to the bucket from these origins. S3 applies CORS per
# bucket, not per prefix; the uploads/ restriction is the key condition in the signed POST
# policy. Terraform owns the whole document: to add an origin, add it to allowed_origins.
resource "aws_s3_bucket_cors_configuration" "main" {
  bucket = aws_s3_bucket.main.id

  cors_rule {
    allowed_methods = ["POST"]
    allowed_origins = var.allowed_origins
    allowed_headers = ["*"]
    max_age_seconds = 3000
  }
}

# ─── SQS queue (one message per uploaded video) ────────────────────────────

resource "aws_sqs_queue" "jobs" {
  name = var.queue_name

  # The worker's heartbeat (M2b §6) extends a running job's message every 50 s, so this only
  # decides how soon a dead worker's job is retried: about two minutes.
  visibility_timeout_seconds = 120

  sqs_managed_sse_enabled = true

  # Retry cap (M2a §4h): a message received twice without being deleted is parked in the
  # dead-letter queue instead of rerunning paid GPU inference forever. The worker reads
  # maxReceiveCount from here at start (M2b §1a), so this is the one place to change it.
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jobs_dlq.arn
    maxReceiveCount     = 2
  })
}

# Parked jobs wait here for 14 days. Inspect with `aws sqs receive-message`; retry after a fix with
# the console's "Start DLQ redrive". SQS moves messages itself, so the worker needs no access.
resource "aws_sqs_queue" "jobs_dlq" {
  name                      = "${var.queue_name}-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

# S3 may only send to the queue with an explicit queue policy (a common gotcha). Scoped to
# this one bucket in this one account.
resource "aws_sqs_queue_policy" "allow_s3" {
  queue_url = aws_sqs_queue.jobs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowS3ToSendUploadEvents"
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.jobs.arn
      Condition = {
        ArnEquals    = { "aws:SourceArn" = aws_s3_bucket.main.arn }
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })
}

# Tell the queue about new uploads. Filtered to uploads/ only: later milestones write status/,
# results/, code/ and experiments/ objects to the same bucket, and an unfiltered notification
# would enqueue them as spurious jobs.
resource "aws_s3_bucket_notification" "uploads" {
  bucket = aws_s3_bucket.main.id

  queue {
    queue_arn     = aws_sqs_queue.jobs.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "uploads/"
  }

  # The queue policy must exist first, or S3 refuses to create the notification.
  depends_on = [aws_sqs_queue_policy.allow_s3]
}
