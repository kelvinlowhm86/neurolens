# ─── Dead-letter handler and reaper Lambdas (M3a §7) ───────────────────────
#
# Both settle or refund jobs: the dead-letter handler every job that failed twice, the reaper (every
# hour, always on, M3b §6) any job left stuck. Not in the VPC: they reach Aurora
# through the Data API and S3 through its public endpoint, both IAM-checked. One zip with only the
# pure-Python modules they import (no numpy); it changes only when those files do.

locals {
  repo_root = "${path.module}/../.."
  lambda_files = [
    "neurolens/__init__.py",
    "neurolens/db.py",
    "neurolens/billing.py",
    "neurolens/pricing.py",
    "neurolens/storage.py",
    "neurolens/lambdas/__init__.py",
    "neurolens/lambdas/dlq_handler.py",
    "neurolens/lambdas/reaper.py",
  ]
  m3a_lambdas = {
    dlq    = { name = "neurolens-dlq-handler", handler = "neurolens.lambdas.dlq_handler.handler" }
    reaper = { name = "neurolens-reaper", handler = "neurolens.lambdas.reaper.handler" }
  }
}

data "archive_file" "m3a_lambdas" {
  type        = "zip"
  output_path = "${path.module}/.build/m3a_lambdas.zip"

  dynamic "source" {
    for_each = local.lambda_files
    content {
      content  = file("${local.repo_root}/${source.value}")
      filename = source.value
    }
  }
}

resource "aws_iam_role" "m3a_lambda" {
  for_each = local.m3a_lambdas

  name                 = "${each.value.name}${local.iam_suffix}"
  permissions_boundary = local.boundary_arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
  tags = { Milestone = "M3a" }
}

resource "aws_iam_role_policy" "m3a_lambda" {
  for_each = local.m3a_lambdas

  name = each.value.name
  role = aws_iam_role.m3a_lambda[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(local.db_access_statements, [
      {
        # Is there a result (settle) or an upload (reaper)?
        Sid      = "ReadResultsAndUploads"
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = ["${aws_s3_bucket.main.arn}/results/*", "${aws_s3_bucket.main.arn}/uploads/*"]
      },
      {
        # Without it S3 answers 403, not 404, for a missing object. No s3:prefix condition: a HEAD
        # carries no prefix, so the condition would bring the 403 back (as for the worker).
        Sid      = "ListBucketSoMissingObjectsGive404"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.main.arn
      },
      {
        Sid      = "OwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.m3a_lambda[each.key].arn}:*"
      },
      ], each.key == "dlq" ? [{
        Sid      = "ReadTheDeadLetterQueue"
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.jobs_dlq.arn
    }] : [])
  })
}

# Log lines contain S3 keys with user ids: kept 14 days, and removed by `terraform destroy`.
resource "aws_cloudwatch_log_group" "m3a_lambda" {
  for_each = local.m3a_lambdas

  name              = "/aws/lambda/${each.value.name}"
  retention_in_days = 14
  tags              = { Milestone = "M3a" }
}

resource "aws_lambda_function" "m3a" {
  for_each = local.m3a_lambdas

  function_name    = each.value.name
  role             = aws_iam_role.m3a_lambda[each.key].arn
  runtime          = "python3.12"
  handler          = each.value.handler
  filename         = data.archive_file.m3a_lambdas.output_path
  source_code_hash = data.archive_file.m3a_lambdas.output_base64sha256
  timeout          = 120 # waking Aurora alone can take about 60 s
  memory_size      = 256

  environment {
    variables = {
      NEUROLENS_DB_CLUSTER_ARN = aws_rds_cluster.db.arn
      NEUROLENS_DB_SECRET_ARN  = local.db_secret_arn
      NEUROLENS_DB_NAME        = local.db_name
      NEUROLENS_S3_BUCKET      = aws_s3_bucket.main.bucket
    }
  }

  depends_on = [aws_cloudwatch_log_group.m3a_lambda, aws_iam_role_policy.m3a_lambda]
  tags       = { Milestone = "M3a" }
}

# One message at a time. A raised error (a worker still holds the job) leaves the message for the
# dead-letter queue's visibility timeout, then it is tried again.
resource "aws_lambda_event_source_mapping" "dlq" {
  event_source_arn = aws_sqs_queue.jobs_dlq.arn
  function_name    = aws_lambda_function.m3a["dlq"].arn
  batch_size       = 1
}

# Always on, owned by Terraform alone (M3b §6). Each run wakes Aurora for about 5 minutes (it pauses
# after 300 s idle): about $4 a month. A stuck job waits at most about 70 minutes (its 10- or 60-minute
# threshold plus the hour); the dead-letter handler still refunds the common failures in seconds.
resource "aws_cloudwatch_event_rule" "reaper" {
  name                = "neurolens-reaper-hourly"
  description         = "Run the NeuroLens reaper (settles or refunds stuck jobs) every hour."
  schedule_expression = "rate(1 hour)"
  state               = "ENABLED"
  tags                = { Milestone = "M3b" }
}

resource "aws_cloudwatch_event_target" "reaper" {
  rule = aws_cloudwatch_event_rule.reaper.name
  arn  = aws_lambda_function.m3a["reaper"].arn
}

resource "aws_lambda_permission" "events_invoke_reaper" {
  statement_id  = "AllowReaperSchedule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.m3a["reaper"].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.reaper.arn
}
