# ─── Roles for machines (M2a §2, §4b) ──────────────────────────────────────
#
# Every role carries the permissions boundary from infra/iam/neurolens-role-boundary.json (created by
# hand in the console). The deploy user may only create roles that carry it, so AWS refuses any role
# here without it.

locals {
  account_id     = data.aws_caller_identity.current.account_id
  boundary_arn   = "arn:aws:iam::${local.account_id}:policy/neurolens-role-boundary"
  ssm_core_arn   = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
  worker_asg     = "neurolens-workers"
  worker_asg_arn = "arn:aws:autoscaling:us-east-1:${local.account_id}:autoScalingGroup:*:autoScalingGroupName/${local.worker_asg}"
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
  name                 = "neurolens-build"
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
        Resource = "arn:aws:ssm:us-east-1:${local.account_id}:parameter/neurolens/hf_token"
      },
      {
        Sid      = "DecryptItThroughParameterStore"
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = "*"
        Condition = {
          StringEquals = { "kms:ViaService" = "ssm.us-east-1.amazonaws.com" }
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
  name = "neurolens-build"
  role = aws_iam_role.build.name
}

# ─── GPU workers ───────────────────────────────────────────────────────────

resource "aws_iam_role" "worker" {
  name                 = "neurolens-worker"
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
        Sid      = "DeleteRejectedUploads"
        Effect   = "Allow"
        Action   = "s3:DeleteObject"
        Resource = "${aws_s3_bucket.main.arn}/uploads/*"
      },
      {
        Sid      = "ReadJobQueue"
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.jobs.arn
      },
      {
        Sid      = "EndOwnMachine"
        Effect   = "Allow"
        Action   = "autoscaling:TerminateInstanceInAutoScalingGroup"
        Resource = local.worker_asg_arn
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "worker_ssm" {
  role       = aws_iam_role.worker.name
  policy_arn = local.ssm_core_arn
}

resource "aws_iam_instance_profile" "worker" {
  name = "neurolens-worker"
  role = aws_iam_role.worker.name
}

# ─── NAT instance (Session Manager only, for debugging it without SSH) ─────

resource "aws_iam_role" "nat" {
  name                 = "neurolens-nat"
  assume_role_policy   = data.aws_iam_policy_document.ec2_assume.json
  permissions_boundary = local.boundary_arn
}

resource "aws_iam_role_policy_attachment" "nat_ssm" {
  role       = aws_iam_role.nat.name
  policy_arn = local.ssm_core_arn
}

resource "aws_iam_instance_profile" "nat" {
  name = "neurolens-nat"
  role = aws_iam_role.nat.name
}
