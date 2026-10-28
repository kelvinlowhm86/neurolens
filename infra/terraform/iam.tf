# ─── Roles for the GPU container and the image build (M3e §9) ─────────────
#
# Every role carries the permissions boundary from infra/iam/neurolens-role-boundary.json
# (created by hand in the console). The deploy user may only create roles that carry it, so AWS
# refuses any role here without it.

locals {
  account_id   = data.aws_caller_identity.current.account_id
  boundary_arn = "arn:aws:iam::${local.account_id}:policy/neurolens-role-boundary"
  # IAM names are global (shared by every region): a deployment in another region (M2a §4i) adds
  # its region to them. us-east-1 keeps the original names, so its live roles are not replaced.
  iam_suffix = var.region == "us-east-1" ? "" : "-${var.region}"
}

# ─── SageMaker execution role (the GPU container) ─────────────────────────

resource "aws_iam_role" "sagemaker" {
  name                 = "neurolens-sagemaker${local.iam_suffix}"
  permissions_boundary = local.boundary_arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "sagemaker.amazonaws.com" }
    }]
  })
  tags = { Milestone = "M3e" }
}

resource "aws_iam_role_policy" "sagemaker" {
  name = "neurolens-sagemaker"
  role = aws_iam_role.sagemaker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(local.db_access_statements, [
      {
        # SageMaker copies model-data/ into /opt/ml/model and the container reads the upload.
        # results/ is read for the "already has a result" check before a claim.
        Sid      = "ReadModelDataUploadsAndResults"
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = [for p in ["model-data", "uploads", "results"] : "${aws_s3_bucket.main.arn}/${p}/*"]
      },
      {
        # Without ListBucket, S3 answers a missing result with 403 instead of 404, which
        # object_exists treats as a failure. Model download also lists model-data/.
        # No s3:prefix condition: a HEAD or GET carries no prefix.
        Sid      = "ListBucketSoMissingObjectsGive404"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.main.arn
      },
      {
        # Our result and brain under academic/, the shared video under dataset/, and the boot
        # record (M3f §5b).
        Sid    = "WriteResultsDatasetAndBootRecords"
        Effect = "Allow"
        Action = "s3:PutObject"
        Resource = [
          "${aws_s3_bucket.main.arn}/results/*",
          "${aws_s3_bucket.main.arn}/dataset/*",
          "${aws_s3_bucket.main.arn}/academic/*",
          "${aws_s3_bucket.main.arn}/experiments/boots/*",
          "${aws_s3_bucket.main.arn}/sagemaker-output/*",
        ]
      },
      {
        # A refund deletes every file the job has (M3e §4c, M3f §5c).
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
        # The image lives in our ECR repository. GetAuthorizationToken has no resource to scope.
        Sid      = "GetEcrAuthorizationToken"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Sid      = "PullTheWorkerImage"
        Effect   = "Allow"
        Action   = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
        Resource = aws_ecr_repository.worker.arn
      },
      {
        # SageMaker writes the container's log here (/aws/sagemaker/Endpoints/neurolens).
        Sid    = "OwnContainerLogs"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = [
          "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/sagemaker/Endpoints/${local.endpoint_name}*",
          "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/sagemaker/Endpoints/${local.endpoint_name}*:*",
        ]
      },
      {
        # SageMaker publishes the instance metrics (CPU, memory, GPU, disk) with this role; without
        # it there are none, and Experiment 2 cannot count running instances (M3e §10).
        # PutMetricData has no resource, so the namespace condition is the scope.
        Sid       = "PublishEndpointInstanceMetrics"
        Effect    = "Allow"
        Action    = "cloudwatch:PutMetricData"
        Resource  = "*"
        Condition = { StringEquals = { "cloudwatch:namespace" = "/aws/sagemaker/Endpoints" } }
      },
      {
        # The endpoint's ErrorTopic (M3e §5).
        Sid      = "PublishGpuErrors"
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = aws_sns_topic.gpu_errors.arn
      },
    ])
  })
}

# ─── CodeBuild role (builds and pushes the image) ─────────────────────────

resource "aws_iam_role" "codebuild" {
  name                 = "neurolens-codebuild${local.iam_suffix}"
  permissions_boundary = local.boundary_arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "codebuild.amazonaws.com" }
    }]
  })
  tags = { Milestone = "M3e" }
}

resource "aws_iam_role_policy" "codebuild" {
  name = "neurolens-codebuild"
  role = aws_iam_role.codebuild.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # The source zip infra/build_image.sh uploads. Nothing else in the bucket.
        Sid      = "ReadTheBuildSource"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = "${aws_s3_bucket.main.arn}/build/source.zip"
      },
      {
        Sid       = "ListTheBuildPrefix"
        Effect    = "Allow"
        Action    = "s3:ListBucket"
        Resource  = aws_s3_bucket.main.arn
        Condition = { StringLike = { "s3:prefix" = ["build/", "build/*"] } }
      },
      {
        Sid      = "LocateTheBucket"
        Effect   = "Allow"
        Action   = "s3:GetBucketLocation"
        Resource = aws_s3_bucket.main.arn
      },
      {
        # GetAuthorizationToken has no resource to scope.
        Sid      = "GetEcrAuthorizationToken"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        # The Dockerfile's base is AWS's PyTorch inference image in account 763104351884 (M3e §4d).
        Sid      = "PullTheAwsBaseImage"
        Effect   = "Allow"
        Action   = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
        Resource = "arn:aws:ecr:us-east-1:763104351884:repository/pytorch-inference"
      },
      {
        Sid    = "PushTheWorkerImage"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload", "ecr:PutImage",
        ]
        Resource = aws_ecr_repository.worker.arn
      },
      {
        Sid      = "OwnBuildLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.codebuild.arn}:*"
      },
    ]
  })
}
