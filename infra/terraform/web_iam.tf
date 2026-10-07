# Roles of the web and Stripe webhook functions (M3b §3a, §7b). Each may do only what its code
# needs, and the permissions boundary caps both whatever is written here.

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
        # Cognito client secret, Flask session key, Stripe keys, top-up allowlist, public address.
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
        # Without it S3 answers 403, not 404, for a result not written yet, and the page would get
        # a server error instead of "not ready". No s3:prefix condition: a GET carries none.
        Sid      = "ListBucketSoMissingObjectsGive404"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.main.arn
      },
      {
        # Is processing paused (worker group max 0)? Read-only; AWS offers no narrower resource.
        Sid      = "ReadWorkerGroup"
        Effect   = "Allow"
        Action   = "autoscaling:DescribeAutoScalingGroups"
        Resource = "*"
      },
    ]
    webhook = [
      {
        Sid    = "ReadStripeWebhookParameters"
        Effect = "Allow"
        Action = "ssm:GetParameter"
        Resource = [
          "${local.ssm_arn}/neurolens/web/stripe_webhook_secret",
          "${local.ssm_arn}/neurolens/web/topup_allowlist",
        ]
      },
      local.decrypt_parameters,
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
