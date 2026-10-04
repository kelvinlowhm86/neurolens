# ─── Roles for machines (M2a §2, §4b) ──────────────────────────────────────
#
# Every role carries the permissions boundary from infra/iam/neurolens-role-boundary.json (created by
# hand in the console). The deploy user may only create roles that carry it, so AWS refuses any role
# here without it.

locals {
  account_id   = data.aws_caller_identity.current.account_id
  boundary_arn = "arn:aws:iam::${local.account_id}:policy/neurolens-role-boundary"
  ssm_core_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
  worker_asg   = "neurolens-workers"
  # IAM names are global (shared by every region): a deployment in another region (M2a §4i) adds
  # its region to them. us-east-1 keeps the original names, so its live roles are not replaced.
  iam_suffix     = var.region == "us-east-1" ? "" : "-${var.region}"
  worker_asg_arn = "arn:aws:autoscaling:${var.region}:${local.account_id}:autoScalingGroup:*:autoScalingGroupName/${local.worker_asg}"

  # Only the image build needs the HuggingFace token. The AWS-managed SSM core policy allows reading
  # every parameter and the boundary allows /neurolens/*, so the worker and NAT roles deny it
  # explicitly (a Deny always wins over an Allow). M2a §4b.
  deny_neurolens_parameters = {
    Sid      = "NoNeurolensParameters"
    Effect   = "Deny"
    Action   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath", "ssm:GetParameterHistory"]
    Resource = "arn:aws:ssm:${var.region}:${local.account_id}:parameter/neurolens/*"
  }
}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

# ─── Image-build instance ──────────────────────────────────────────────────

resource "aws_iam_role" "build" {
  name                 = "neurolens-build${local.iam_suffix}"
  assume_role_policy   = data.aws_iam_policy_document.ec2_assume.json
  permissions_boundary = local.boundary_arn
}

resource "aws_iam_role_policy" "build" {
  name = "neurolens-build"
  role = aws_iam_role.build.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadHuggingFaceToken"
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = "arn:aws:ssm:${var.region}:${local.account_id}:parameter/neurolens/hf_token"
      },
      {
        Sid      = "DecryptItThroughParameterStore"
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = "*"
        Condition = {
          StringEquals = { "kms:ViaService" = "ssm.${var.region}.amazonaws.com" }
        }
      },
      {
        Sid    = "ReadCodeAndTestClip"
        Effect = "Allow"
        Action = "s3:GetObject"
        Resource = [
          "${aws_s3_bucket.main.arn}/code/*",
          "${aws_s3_bucket.main.arn}/smoke/*",
        ]
      },
      {
        Sid      = "ReadWriteWeights"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.main.arn}/models/*"
      },
      {
        Sid       = "ListWeights"
        Effect    = "Allow"
        Action    = "s3:ListBucket"
        Resource  = aws_s3_bucket.main.arn
        Condition = { StringLike = { "s3:prefix" = ["models/", "models/*"] } }
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "build_ssm" {
  role       = aws_iam_role.build.name
  policy_arn = local.ssm_core_arn
}

resource "aws_iam_instance_profile" "build" {
  name = "neurolens-build${local.iam_suffix}"
  role = aws_iam_role.build.name
}

# ─── GPU workers ───────────────────────────────────────────────────────────

resource "aws_iam_role" "worker" {
  name                 = "neurolens-worker${local.iam_suffix}"
  assume_role_policy   = data.aws_iam_policy_document.ec2_assume.json
  permissions_boundary = local.boundary_arn
}

resource "aws_iam_role_policy" "worker" {
  name = "neurolens-worker"
  role = aws_iam_role.worker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadInputsCodeWeightsResults"
        Effect = "Allow"
        Action = "s3:GetObject"
        Resource = [for p in ["uploads", "code", "models", "results"] :
        "${aws_s3_bucket.main.arn}/${p}/*"]
      },
      {
        # Without ListBucket, S3 answers a request for a missing object with 403 instead of 404,
        # which the worker would treat as a failure, not GONE. No s3:prefix condition: a HEAD or GET
        # carries no prefix, so the condition would fail and bring the 403 back.
        Sid      = "ListBucketSoMissingObjectsGive404"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.main.arn
      },
      {
        Sid      = "WriteResults"
        Effect   = "Allow"
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.main.arn}/results/*"
      },
      {
        # Job status objects (M2b §4): read, then written back with the new stage.
        Sid      = "ReadWriteJobStatus"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.main.arn}/status/*"
      },
      {
        # Boot records for the cold-start table (M2b §10). Write only.
        Sid      = "WriteBootRecords"
        Effect   = "Allow"
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.main.arn}/experiments/*"
      },
      {
        Sid      = "DeleteRejectedUploads"
        Effect   = "Allow"
        Action   = "s3:DeleteObject"
        Resource = "${aws_s3_bucket.main.arn}/uploads/*"
      },
      {
        Sid    = "ReadJobQueue"
        Effect = "Allow"
        # ChangeMessageVisibility: the heartbeat and the fast release (M2b §6). GetQueueAttributes:
        # the worker reads maxReceiveCount from the queue's redrive policy at start.
        Action = [
          "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes",
          "sqs:ChangeMessageVisibility",
        ]
        Resource = aws_sqs_queue.jobs.arn
      },
      # No Auto Scaling permission at all: from M2b only AWS decides how many workers run (§2).
      local.deny_neurolens_parameters,
    ]
  })
}

resource "aws_iam_role_policy_attachment" "worker_ssm" {
  role       = aws_iam_role.worker.name
  policy_arn = local.ssm_core_arn
}

resource "aws_iam_instance_profile" "worker" {
  name = "neurolens-worker${local.iam_suffix}"
  role = aws_iam_role.worker.name
}

# ─── Circuit breaker Lambda (M2b §2c) ──────────────────────────────────────

resource "aws_iam_role" "breaker" {
  name                 = "neurolens-breaker${local.iam_suffix}"
  permissions_boundary = local.boundary_arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
  tags = { Milestone = "M2b" }
}

resource "aws_iam_role_policy" "breaker" {
  name = "neurolens-breaker"
  role = aws_iam_role.breaker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Describe calls cannot be limited to one group (AWS needs "*"). Read only.
        Sid      = "ReadGroups"
        Effect   = "Allow"
        Action   = "autoscaling:DescribeAutoScalingGroups"
        Resource = "*"
      },
      {
        Sid      = "SetWorkerGroupToZero"
        Effect   = "Allow"
        Action   = "autoscaling:UpdateAutoScalingGroup"
        Resource = local.worker_asg_arn
      },
      {
        Sid      = "OwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.breaker.arn}:*"
      },
    ]
  })
}

# ─── NAT instance (Session Manager only, for debugging it without SSH) ─────

resource "aws_iam_role" "nat" {
  name                 = "neurolens-nat${local.iam_suffix}"
  assume_role_policy   = data.aws_iam_policy_document.ec2_assume.json
  permissions_boundary = local.boundary_arn
}

resource "aws_iam_role_policy" "nat" {
  name = "neurolens-nat"
  role = aws_iam_role.nat.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [local.deny_neurolens_parameters]
  })
}

resource "aws_iam_role_policy_attachment" "nat_ssm" {
  role       = aws_iam_role.nat.name
  policy_arn = local.ssm_core_arn
}

resource "aws_iam_instance_profile" "nat" {
  name = "neurolens-nat${local.iam_suffix}"
  role = aws_iam_role.nat.name
}
