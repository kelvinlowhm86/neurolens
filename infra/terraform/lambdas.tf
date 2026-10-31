# ─── The job Lambdas (M3a §7, M3e §3, §5, §6c) ────────────────────────────
#
# One zip with every pure-Python module they import (no numpy, no torch): the dead-letter
# handler, the reaper, the dispatcher, the SageMaker error handler and the GPU guard. They run
# outside the VPC: Aurora is reached through the Data API and S3 through its public endpoint.
# The dispatcher also needs the static ffprobe layer (infra/build_ffprobe_layer.sh).

locals {
  repo_root = "${path.module}/../.."
  config    = jsondecode(file("${local.repo_root}/config.json"))
  lambda_files = [
    "config.json",
    "neurolens/__init__.py",
    "neurolens/db.py",
    "neurolens/billing.py",
    "neurolens/media.py",
    "neurolens/pricing.py",
    "neurolens/report_prompt.py",
    "neurolens/results.py",
    "neurolens/settings.py",
    "neurolens/storage.py",
    "neurolens/lambdas/__init__.py",
    "neurolens/lambdas/dlq_handler.py",
    "neurolens/lambdas/reaper.py",
    "neurolens/lambdas/dispatcher.py",
    "neurolens/lambdas/gpu_failed.py",
    "neurolens/lambdas/gpu_guard.py",
    "neurolens/lambdas/report.py",
  ]
  # gpu_failed does the dead-letter handler's work on a SageMaker notification (§5), so it shares
  # that role's rights.
  m3a_lambdas = {
    dlq        = { name = "neurolens-dlq-handler", handler = "neurolens.lambdas.dlq_handler.handler" }
    reaper     = { name = "neurolens-reaper", handler = "neurolens.lambdas.reaper.handler" }
    gpu_failed = { name = "neurolens-gpu-failed", handler = "neurolens.lambdas.gpu_failed.handler" }
  }
  lambda_env = {
    NEUROLENS_DB_CLUSTER_ARN = aws_rds_cluster.db.arn
    NEUROLENS_DB_SECRET_ARN  = local.db_secret_arn
    NEUROLENS_DB_NAME        = local.db_name
    NEUROLENS_S3_BUCKET      = aws_s3_bucket.main.bucket
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
  tags = { Milestone = "M3e" }
}

resource "aws_iam_role_policy" "m3a_lambda" {
  for_each = local.m3a_lambdas

  name = each.value.name
  role = aws_iam_role.m3a_lambda[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(local.db_access_statements, [
      {
        # Is there a result (settle), or an upload (refund)?
        Sid    = "ReadResultsAndUploads"
        Effect = "Allow"
        Action = "s3:GetObject"
        Resource = [
          "${aws_s3_bucket.main.arn}/results/*",
          "${aws_s3_bucket.main.arn}/uploads/*",
        ]
      },
      {
        # A refund deletes every file the job has (M3a §7b, M3f §5c).
        Sid    = "DeleteAJobsFilesAfterRefund"
        Effect = "Allow"
        Action = "s3:DeleteObject"
        Resource = [
          "${aws_s3_bucket.main.arn}/uploads/*",
          "${aws_s3_bucket.main.arn}/results/*",
          "${aws_s3_bucket.main.arn}/dataset/*",
          "${aws_s3_bucket.main.arn}/academic/*",
        ]
      },
      {
        # Without it S3 answers 403, not 404, for a missing object. No s3:prefix condition: a HEAD
        # carries no prefix, so the condition would bring the 403 back (as for the container).
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
  retention_in_days = var.log_retention_days
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
    variables = local.lambda_env
  }

  depends_on = [aws_cloudwatch_log_group.m3a_lambda, aws_iam_role_policy.m3a_lambda]
  tags       = { Milestone = "M3e" }
}

# One message at a time. A raised error (a container still holds the job) leaves the message for
# the dead-letter queue's visibility timeout, then it is tried again.
resource "aws_lambda_event_source_mapping" "dlq" {
  event_source_arn = aws_sqs_queue.jobs_dlq.arn
  function_name    = aws_lambda_function.m3a["dlq"].arn
  batch_size       = 1
}

# The SageMaker endpoint's ErrorTopic. The subscription must be allowed to invoke the function.
resource "aws_sns_topic_subscription" "gpu_failed" {
  topic_arn = aws_sns_topic.gpu_errors.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.m3a["gpu_failed"].arn
}

resource "aws_lambda_permission" "sns_invoke_gpu_failed" {
  statement_id  = "AllowGpuErrorsTopic"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.m3a["gpu_failed"].function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.gpu_errors.arn
}

# Always on, owned by Terraform alone (M3b §6). Each run wakes Aurora for about 11 minutes (measured;
# it pauses 300 s after the last connection closes). Every 3 hours costs about $3 a month; hourly
# measured about $15. A lost notification is refunded within about 7 hours (M3e §5).
resource "aws_cloudwatch_event_rule" "reaper" {
  name                = "neurolens-reaper-schedule"
  description         = "Run the NeuroLens reaper (settles or refunds stuck jobs) every 3 hours."
  schedule_expression = "rate(3 hours)"
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

# ─── Dispatcher (M3e §3) ──────────────────────────────────────────────────

resource "aws_iam_role" "dispatcher" {
  name                 = "neurolens-dispatcher${local.iam_suffix}"
  permissions_boundary = local.boundary_arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
  tags = { Milestone = "M3e" }
}

resource "aws_iam_role_policy" "dispatcher" {
  name = "neurolens-dispatcher"
  role = aws_iam_role.dispatcher.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(local.db_access_statements, [
      {
        # Batch size 1: receive, settle and delete the upload's message.
        Sid      = "ConsumeTheJobsQueue"
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.jobs.arn
      },
      {
        # Measure the upload and delete it when the job is refused.
        Sid      = "ReadAndDeleteUploads"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.main.arn}/uploads/*"
      },
      {
        # Without it S3 answers 403, not 404, for a missing upload.
        Sid      = "ListBucketSoMissingObjectsGive404"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.main.arn
      },
      {
        Sid      = "InvokeTheEndpoint"
        Effect   = "Allow"
        Action   = "sagemaker:InvokeEndpointAsync"
        Resource = local.endpoint_arn
      },
      {
        # Start the GPU after a dispatch so the cold start does not wait for the alarm (M3f §6a).
        Sid      = "StartTheGpu"
        Effect   = "Allow"
        Action   = ["sagemaker:DescribeEndpoint", "sagemaker:UpdateEndpointWeightsAndCapacities"]
        Resource = local.endpoint_arn
      },
      {
        Sid      = "OwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.dispatcher.arn}:*"
      },
    ])
  })
}

# The static Linux x86-64 ffprobe, built by infra/build_ffprobe_layer.sh. Terraform uploads the
# zip that script makes; it is not created here.
resource "aws_lambda_layer_version" "ffprobe" {
  layer_name          = "neurolens-ffprobe"
  description         = "Static ffprobe for the dispatcher (infra/build_ffprobe_layer.sh)."
  filename            = "${path.module}/.build/ffprobe_layer.zip"
  source_code_hash    = try(filebase64sha256("${path.module}/.build/ffprobe_layer.zip"), null)
  compatible_runtimes = ["python3.12"]
}

resource "aws_cloudwatch_log_group" "dispatcher" {
  name              = "/aws/lambda/neurolens-dispatcher"
  retention_in_days = var.log_retention_days
  tags              = { Milestone = "M3e" }
}

resource "aws_lambda_function" "dispatcher" {
  function_name    = "neurolens-dispatcher"
  role             = aws_iam_role.dispatcher.arn
  runtime          = "python3.12"
  handler          = "neurolens.lambdas.dispatcher.handler"
  filename         = data.archive_file.m3a_lambdas.output_path
  source_code_hash = data.archive_file.m3a_lambdas.output_base64sha256
  layers           = [aws_lambda_layer_version.ffprobe.arn]
  timeout          = 180
  memory_size      = 1024 # ffprobe reads packets: the extra memory is mostly I/O headroom

  environment {
    variables = merge(local.lambda_env, {
      NEUROLENS_GPU_ENDPOINT         = local.endpoint_name
      NEUROLENS_MAX_UPLOAD_BYTES     = tostring(local.config.max_upload_bytes)
      NEUROLENS_MAX_DURATION_SECONDS = tostring(local.config.max_video_duration_seconds)
    })
  }

  depends_on = [aws_cloudwatch_log_group.dispatcher, aws_iam_role_policy.dispatcher]
  tags       = { Milestone = "M3e" }
}

resource "aws_lambda_event_source_mapping" "dispatcher" {
  event_source_arn = aws_sqs_queue.jobs.arn
  function_name    = aws_lambda_function.dispatcher.arn
  batch_size       = 1
}

# ─── GPU guard (M3e §6c) ──────────────────────────────────────────────────

resource "aws_iam_role" "gpu_guard" {
  name                 = "neurolens-gpu-guard${local.iam_suffix}"
  permissions_boundary = local.boundary_arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
  tags = { Milestone = "M3e" }
}

resource "aws_iam_role_policy" "gpu_guard" {
  name = "neurolens-gpu-guard"
  role = aws_iam_role.gpu_guard.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(local.db_access_statements, [
      {
        Sid      = "ReadAndStopOurEndpoint"
        Effect   = "Allow"
        Action   = ["sagemaker:DescribeEndpoint", "sagemaker:UpdateEndpointWeightsAndCapacities"]
        Resource = local.endpoint_arn
      },
      {
        # Is a keep-warm holding the minimum? Read-only; AWS offers no narrower resource.
        Sid      = "ReadScalingTargets"
        Effect   = "Allow"
        Action   = "application-autoscaling:DescribeScalableTargets"
        Resource = "*"
      },
      {
        Sid      = "OwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.gpu_guard.arn}:*"
      },
    ])
  })
}

resource "aws_cloudwatch_log_group" "gpu_guard" {
  name              = "/aws/lambda/neurolens-gpu-guard"
  retention_in_days = var.log_retention_days
  tags              = { Milestone = "M3e" }
}

resource "aws_lambda_function" "gpu_guard" {
  function_name    = "neurolens-gpu-guard"
  role             = aws_iam_role.gpu_guard.arn
  runtime          = "python3.12"
  handler          = "neurolens.lambdas.gpu_guard.handler"
  filename         = data.archive_file.m3a_lambdas.output_path
  source_code_hash = data.archive_file.m3a_lambdas.output_base64sha256
  timeout          = 60
  memory_size      = 256

  environment {
    variables = merge(local.lambda_env, {
      NEUROLENS_GPU_ENDPOINT = local.endpoint_name
    })
  }

  depends_on = [aws_cloudwatch_log_group.gpu_guard, aws_iam_role_policy.gpu_guard]
  tags       = { Milestone = "M3e" }
}

resource "aws_cloudwatch_event_rule" "gpu_guard" {
  name                = "neurolens-gpu-guard-every-10-min"
  description         = "Run the NeuroLens GPU guard (stop a GPU left on with nothing to do)."
  schedule_expression = "rate(10 minutes)"
  state               = "ENABLED"
  tags                = { Milestone = "M3e" }
}

resource "aws_cloudwatch_event_target" "gpu_guard" {
  rule = aws_cloudwatch_event_rule.gpu_guard.name
  arn  = aws_lambda_function.gpu_guard.arn
}

resource "aws_lambda_permission" "events_invoke_gpu_guard" {
  statement_id  = "AllowGpuGuardSchedule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.gpu_guard.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.gpu_guard.arn
}

# ─── The AI report (M3g §5, §8) ───────────────────────────────────────────
#
# Its own Lambda because Bedrock takes ~30-90 s, longer than the web function's 60 s. The
# Anthropic SDK ships as a layer (infra/build_report_layer.sh), so this zip stays dependency-free.
# Reserved concurrency caps parallel Bedrock spend; async retries are off because a failure is
# final and recorded on the job.

locals {
  # config.json names a US inference profile ("us.<model>"); IAM also needs the bare model.
  report_profile_arn = "arn:aws:bedrock:${var.region}:${local.account_id}:inference-profile/${local.config.report.model}"
  report_model       = trimprefix(local.config.report.model, "us.")
}

resource "aws_iam_role" "report" {
  name                 = "neurolens-report${local.iam_suffix}"
  permissions_boundary = local.boundary_arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
  tags = { Milestone = "M3g" }
}

resource "aws_iam_role_policy" "report" {
  name = "neurolens-report"
  role = aws_iam_role.report.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(local.db_access_statements, [
      {
        # The result and frames to read; the report to write and remove.
        Sid      = "ReadInputs"
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.main.arn}/results/*"
      },
      {
        Sid      = "WriteAndRemoveTheReport"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.main.arn}/results/*.report.json"
      },
      {
        # Without it S3 answers 403, not 404, for a missing input. Listing only results/.
        Sid       = "ListResultsSoMissingObjectsGive404"
        Effect    = "Allow"
        Action    = "s3:ListBucket"
        Resource  = aws_s3_bucket.main.arn
        Condition = { StringLike = { "s3:prefix" = ["results/*"] } }
      },
      {
        # The US inference profile in config.json, and the model it routes to in any US region.
        Sid      = "InvokeTheReportModelProfile"
        Effect   = "Allow"
        Action   = "bedrock:InvokeModel"
        Resource = local.report_profile_arn
      },
      {
        Sid       = "InvokeTheReportModelThroughTheProfile"
        Effect    = "Allow"
        Action    = "bedrock:InvokeModel"
        Resource  = "arn:aws:bedrock:*::foundation-model/${local.report_model}"
        Condition = { StringEquals = { "bedrock:InferenceProfileArn" = local.report_profile_arn } }
      },
      {
        Sid      = "OwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.report.arn}:*"
      },
    ])
  })
}

resource "aws_lambda_layer_version" "report" {
  layer_name          = "neurolens-report"
  description         = "Anthropic SDK for Bedrock (infra/build_report_layer.sh)."
  filename            = "${path.module}/.build/report_layer.zip"
  source_code_hash    = try(filebase64sha256("${path.module}/.build/report_layer.zip"), null)
  compatible_runtimes = ["python3.12"]
}

resource "aws_cloudwatch_log_group" "report" {
  name              = "/aws/lambda/neurolens-report"
  retention_in_days = var.log_retention_days
  tags              = { Milestone = "M3g" }
}

resource "aws_lambda_function" "report" {
  function_name                  = "neurolens-report"
  role                           = aws_iam_role.report.arn
  runtime                        = "python3.12"
  handler                        = "neurolens.lambdas.report.handler"
  filename                       = data.archive_file.m3a_lambdas.output_path
  source_code_hash               = data.archive_file.m3a_lambdas.output_base64sha256
  layers                         = [aws_lambda_layer_version.report.arn]
  # Three SDK attempts of up to 240 s each fit (M3g §5a); stale_minutes stays above it.
  timeout                        = 900
  memory_size                    = 512
  reserved_concurrent_executions = 5

  environment {
    variables = merge(local.lambda_env, {
      NEUROLENS_AWS_REGION = var.region
    })
  }

  depends_on = [aws_cloudwatch_log_group.report, aws_iam_role_policy.report]
  tags       = { Milestone = "M3g" }
}

# A queued request older than 4 min is dropped: started by then, it ends within the 5-min timeout,
# before the 10-min stale point lets the user claim again, so two runs never overlap.
resource "aws_lambda_function_event_invoke_config" "report" {
  function_name                = aws_lambda_function.report.function_name
  maximum_retry_attempts       = 0
  maximum_event_age_in_seconds = 240
}
