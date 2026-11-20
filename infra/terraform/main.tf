data "aws_caller_identity" "current" {}

# ─── S3 bucket ─────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "main" {
  bucket = var.bucket_name

  # terraform destroy may empty this bucket first (the uploads/results inside it are disposable).
  force_destroy = true
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

# Refuse any request that is not over HTTPS, including the presigned POST and any GET. CloudFront
# (only this distribution) may read the site/ prefix and nothing else: uploads and
# results stay unreachable through it.
resource "aws_s3_bucket_policy" "main" {
  bucket = aws_s3_bucket.main.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
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
      },
      {
        Sid       = "CloudFrontReadsTheSiteOnly"
        Effect    = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.main.arn}/site/*"
        Condition = {
          StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.site.arn }
        }
      },
    ]
  })

  # The public access block must exist before a policy is attached.
  depends_on = [aws_s3_bucket_public_access_block.main]
}

# Prefix-scoped expiry, not one blanket rule. Days are the smallest unit S3 lifecycle uses.
#   uploads/   upload_retention_days   the input videos (job status lives in Aurora)
#   results/   result_retention_days   how long a result stays viewable / downloadable
#   dataset/   research.dataset_retention_days   shared videos and curves, kept for research
#   sagemaker-output/     1 d    SageMaker's async answers; the job row and results/ are the record
#   build/                7 d    source.zip for the image build; one build needs it only that day
resource "aws_s3_bucket_lifecycle_configuration" "main" {
  bucket = aws_s3_bucket.main.id

  rule {
    id     = "expire-uploads"
    status = "Enabled"
    filter {
      prefix = "uploads/"
    }
    expiration {
      days = local.config.upload_retention_days
    }
  }

  rule {
    id     = "expire-results"
    status = "Enabled"
    filter {
      prefix = "results/"
    }
    expiration {
      days = local.config.result_retention_days
    }
  }

  rule {
    id     = "expire-dataset"
    status = "Enabled"
    filter {
      prefix = "dataset/"
    }
    expiration {
      days = local.config.research.dataset_retention_days
    }
  }

  rule {
    id     = "expire-sagemaker-output"
    status = "Enabled"
    filter {
      prefix = "sagemaker-output/"
    }
    expiration {
      days = 1
    }
  }

  rule {
    id     = "expire-build"
    status = "Enabled"
    filter {
      prefix = "build/"
    }
    expiration {
      days = 7
    }
  }
}

# Lets the browser POST a file straight to the bucket from these origins. S3 applies CORS per
# bucket, not per prefix; the uploads/ restriction is the key condition in the signed POST
# policy. Terraform owns the whole document: to add an origin, add it to allowed_origins (the CloudFront address is always included).
resource "aws_s3_bucket_cors_configuration" "main" {
  bucket = aws_s3_bucket.main.id

  cors_rule {
    allowed_methods = ["POST", "GET"]
    allowed_origins = concat(var.allowed_origins, [local.public_base_url])
    allowed_headers = ["*"]
    max_age_seconds = 3000
  }
}

# ─── SQS queue (one message per uploaded video) ────────────────────────────

resource "aws_sqs_queue" "jobs" {
  name = var.queue_name

  # The cancel window: the message waits before the dispatcher starts a GPU.
  delay_seconds = local.config.queue.cancel_seconds

  # The dispatcher's time per message (AWS asks for six times the function timeout), so a slow
  # dispatch is not redelivered while it is still invoking.
  visibility_timeout_seconds = local.config.queue.visibility_seconds

  sqs_managed_sse_enabled = true

  # A message that fails twice goes to the dead-letter queue, whose handler refunds the job. No automatic retry on the GPU: the container and the error handler settle a
  # failed request.
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jobs_dlq.arn
    maxReceiveCount     = local.config.queue.max_receives
  })
}

# Jobs that failed twice. The dead-letter handler Lambda (lambdas.tf) settles or refunds
# each one; SQS moves messages here itself, so no container needs queue access. A message the
# handler cannot finish yet (a container still holds the job) is tried again after the visibility timeout,
# which must be at least the Lambda's 120 s timeout (AWS recommends 6 times it); messages are kept
# 14 days, the reaper being the backstop.
resource "aws_sqs_queue" "jobs_dlq" {
  name                       = "${var.queue_name}-dlq"
  message_retention_seconds  = 1209600
  visibility_timeout_seconds = local.config.queue.dlq_visibility_seconds
  sqs_managed_sse_enabled    = true
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

# Tell the queue about new uploads. Filtered to uploads/ only: the app also writes
# results/ and dataset/ objects to the same bucket, and an unfiltered notification
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
