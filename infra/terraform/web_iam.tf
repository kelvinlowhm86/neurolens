# Role of the web function (M3b §3a, M3c §5, §7). It may do only what its code needs, and the
# permissions boundary caps it whatever is written here.

locals {
  ssm_arn = "arn:aws:ssm:${var.region}:${local.account_id}:parameter"

  # Parameter Store secrets are encrypted with AWS's default key; decrypting is allowed only when
  # Parameter Store asks on the role's behalf.
  decrypt_parameters = {
    Sid       = "DecryptParametersThroughParameterStoreOnly"
    Effect    = "Allow"
    Action    = "kms:Decrypt"
    Resource  = "*"
    Condition = { StringEquals = { "kms:ViaService" = "ssm.${var.region}.amazonaws.com" } }
  }

  web_role_statements = {
    web = [
      {
        # Cognito client secret, Flask session key, public address.
        Sid      = "ReadOwnParameters"
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = "${local.ssm_arn}/neurolens/web/*"
      },
      local.decrypt_parameters,
      {
        # Presigned POSTs are signed with the role's own credentials, so the role itself must be
        # allowed the upload; the signed policy narrows each one to a single key and size.
        Sid      = "SignUploads"
        Effect   = "Allow"
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.main.arn}/uploads/*"
      },
      {
        Sid      = "ReadResults"
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.main.arn}/results/*"
      },
      {
        # A presigned replay link works only if the signer may read the key too.
        Sid    = "ReadVideos"
        Effect = "Allow"
        Action = "s3:GetObject"
        Resource = [
          "${aws_s3_bucket.main.arn}/uploads/*",
          "${aws_s3_bucket.main.arn}/dataset/*",
        ]
      },
      {
        # Without it S3 answers 403, not 404, for a result not written yet, and the page would get
        # a server error instead of "not ready". No s3:prefix condition: a GET carries none.
        Sid      = "ListBucketSoMissingObjectsGive404"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.main.arn
      },
      {
        # Is processing paused (endpoint scaling max 0)? Read-only; AWS offers no narrower resource.
        Sid      = "ReadScalingTargets"
        Effect   = "Allow"
        Action   = "application-autoscaling:DescribeScalableTargets"
        Resource = "*"
      },
      {
        # Deleting a video or the whole account removes its upload, result and dataset files
        # (M3c §4, §5). Missing keys are fine.
        Sid    = "DeleteJobFiles"
        Effect = "Allow"
        Action = "s3:DeleteObject"
        Resource = [
          "${aws_s3_bucket.main.arn}/uploads/*",
          "${aws_s3_bucket.main.arn}/results/*",
          "${aws_s3_bucket.main.arn}/dataset/*",
        ]
      },
      {
        # A real audience-retention CSV for a consented job (M3c §3c).
        Sid      = "StoreRetentionCsv"
        Effect   = "Allow"
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.main.arn}/dataset/*/retention.csv"
      },
      {
        # Account deletion removes the user's logins from our pool only (M3c §5).
        Sid      = "DeleteCognitoLogins"
        Effect   = "Allow"
        Action   = ["cognito-idp:ListUsers", "cognito-idp:AdminDeleteUser"]
        Resource = aws_cognito_user_pool.users.arn
      },
    ]
  }
}

resource "aws_iam_role" "web" {
  for_each = local.web_lambdas

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
  tags = { Milestone = "M3b" }
}

resource "aws_iam_role_policy" "web" {
  for_each = local.web_lambdas

  name = each.value.name
  role = aws_iam_role.web[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(local.db_access_statements, local.web_role_statements[each.key], [
      {
        Sid      = "OwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.web[each.key].arn}:*"
      },
    ])
  })
}
