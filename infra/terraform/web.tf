# The public website's front door and sign-in (M3b §2a, §3b). Day one builds the CloudFront
# address with the page origin and Cognito with Google, so the sign-in round trip can be checked
# before any web code exists; the Lambda API origin and the web and webhook functions follow.

# ─── CloudFront: one HTTPS address for the page and the API ───────────────

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "neurolens-site"
  description                       = "CloudFront signs its requests to the bucket's site/ prefix (the bucket stays private)."
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# CloudFront signs its requests to the web function's URL (SigV4), so only this distribution can call it.
resource "aws_cloudfront_origin_access_control" "api" {
  name                              = "neurolens-api"
  description                       = "CloudFront signs its requests to the neurolens-web function URL."
  origin_access_control_origin_type = "lambda"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

# Cookies, query strings and headers reach Flask; the Host header is left out because Lambda needs its own.
data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

locals {
  api_paths = ["/api/*", "/login", "/logout", "/auth/*", "/healthz"]
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  comment             = "NeuroLens website"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"

  origin {
    origin_id                = "site"
    domain_name              = aws_s3_bucket.main.bucket_regional_domain_name
    origin_path              = "/site"
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  origin {
    origin_id                = "api"
    domain_name              = trimsuffix(trimprefix(aws_lambda_function_url.web.function_url, "https://"), "/")
    origin_access_control_id = aws_cloudfront_origin_access_control.api.id

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
      origin_read_timeout    = 60
    }
  }

  dynamic "ordered_cache_behavior" {
    for_each = local.api_paths
    content {
      path_pattern             = ordered_cache_behavior.value
      target_origin_id         = "api"
      viewer_protocol_policy   = "redirect-to-https"
      allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
      cached_methods           = ["GET", "HEAD"]
      cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
      origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    }
  }

  default_cache_behavior {
    target_origin_id       = "site"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.caching_optimized.id
    compress               = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = { Milestone = "M3b" }
}

locals {
  public_base_url = "https://${aws_cloudfront_distribution.site.domain_name}"
  cognito_domain  = "${var.auth_domain_prefix}.auth.${var.region}.amazoncognito.com"
  cognito_issuer  = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.users.id}"
}

# ─── Cognito: sign-in with Google or email and password ───────────────────

resource "aws_cognito_user_pool" "users" {
  name           = "neurolens-users"
  user_pool_tier = "LITE" # set explicitly: new pools otherwise default to Essentials

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"] # a new email user confirms with a code before signing in

  admin_create_user_config {
    allow_admin_create_user_only = false # self sign-up: new accounts start with 0 credit
  }

  # A changed email is not used until verified, so linking by verified email stays sound.
  user_attribute_update_settings {
    attributes_require_verification_before_update = ["email"]
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # Cognito's own sender (about 50 emails a day): enough for this project.
  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  tags = { Milestone = "M3b" }
}

# The Google OAuth client's secret, stored once by hand as a SecureString (M3b §2a), so no apply
# needs it in the shell. It also ends up in the encrypted Terraform state, as for any provider.
data "aws_ssm_parameter" "google_client_secret" {
  name = "/neurolens/terraform/google_client_secret"
}

resource "aws_cognito_identity_provider" "google" {
  user_pool_id  = aws_cognito_user_pool.users.id
  provider_name = "Google"
  provider_type = "Google"

  # Cognito fills in Google's fixed endpoints itself; listing them keeps every plan from showing
  # a change that removes them.
  provider_details = {
    client_id                     = var.google_client_id
    client_secret                 = data.aws_ssm_parameter.google_client_secret.value
    authorize_scopes              = "openid email"
    attributes_url                = "https://people.googleapis.com/v1/people/me?personFields="
    attributes_url_add_attributes = "true"
    authorize_url                 = "https://accounts.google.com/o/oauth2/v2/auth"
    oidc_issuer                   = "https://accounts.google.com"
    token_request_method          = "POST"
    token_url                     = "https://www.googleapis.com/oauth2/v4/token"
  }

  # Mapped email addresses arrive unverified unless email_verified is mapped too.
  attribute_mapping = {
    email          = "email"
    email_verified = "email_verified"
    username       = "sub"
  }
}

resource "aws_cognito_user_pool_client" "web" {
  name         = "neurolens-web"
  user_pool_id = aws_cognito_user_pool.users.id

  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email"]
  supported_identity_providers         = ["COGNITO", aws_cognito_identity_provider.google.provider_name]
  callback_urls                        = ["${local.public_base_url}/auth/callback"]
  logout_urls                          = ["${local.public_base_url}/"]

  # write_attributes and read_attributes stay at Cognito's defaults (every standard attribute):
  # the Google-mapped email must be writable or sign-in mappings fail, and Cognito refuses
  # email_verified in an explicit list ("Invalid write attributes"). Users never get tokens that
  # could rewrite their own attributes (the server keeps them), and a changed email is unused
  # until verified (attributes_require_verification_before_update).
}

resource "aws_cognito_user_pool_domain" "users" {
  domain                = var.auth_domain_prefix
  user_pool_id          = aws_cognito_user_pool.users.id
  managed_login_version = 1 # the classic hosted pages; the newer managed login needs Essentials
}

# ─── The web function and the Stripe webhook function (M3b §3a, §7b) ──────
#
# Both run the same zip outside the VPC: they reach Cognito, Parameter Store, S3 and the Data API
# over AWS's public endpoints, so they never depend on the NAT Gateway. Their roles are in
# web_iam.tf. Terraform uploads the zip only when it creates the functions (build it first with
# infra/build_web_lambda.sh); after that infra/deploy_web.sh ships code, so an apply for anything
# else (the NAT Gateway switch) never needs a build and never changes the running code.

locals {
  web_zip = "${local.repo_root}/build/web_lambda.zip"
  db_env = {
    NEUROLENS_DB_CLUSTER_ARN = aws_rds_cluster.db.arn
    NEUROLENS_DB_SECRET_ARN  = local.db_secret_arn
    NEUROLENS_DB_NAME        = local.db_name
    NEUROLENS_S3_BUCKET      = aws_s3_bucket.main.bucket
    NEUROLENS_AWS_REGION     = var.region
    NEUROLENS_DEPLOYED       = "1"
  }
  web_lambdas = {
    web = {
      name    = "neurolens-web"
      handler = "neurolens.web.lambda_handler.handler"
      environment = {
        NEUROLENS_AUTH_MODE      = "cognito"
        NEUROLENS_COGNITO_ISSUER = local.cognito_issuer
        NEUROLENS_COGNITO_DOMAIN = local.cognito_domain
        NEUROLENS_WORKER_GROUP   = local.worker_asg
      }
    }
    webhook = {
      name        = "neurolens-stripe-webhook"
      handler     = "neurolens.web.stripe_webhook.handler"
      environment = {}
    }
  }
}

# CloudFront's address, and the Cognito client whose callback address contains it, cannot be
# environment variables of the function CloudFront fronts (its URL is CloudFront's origin: a
# cycle), so the web function reads them from here at startup.
resource "aws_ssm_parameter" "web_deployment" {
  for_each = {
    public_base_url   = local.public_base_url
    cognito_client_id = aws_cognito_user_pool_client.web.id
  }

  name  = "/neurolens/web/${each.key}"
  type  = "String"
  value = each.value
  tags  = { Milestone = "M3b" }
}

# Log lines contain user ids and email addresses: kept 14 days, and removed by `terraform destroy`.
resource "aws_cloudwatch_log_group" "web" {
  for_each = local.web_lambdas

  name              = "/aws/lambda/${each.value.name}"
  retention_in_days = 14
  tags              = { Milestone = "M3b" }
}

resource "aws_lambda_function" "web" {
  for_each = local.web_lambdas

  function_name = each.value.name
  role          = aws_iam_role.web[each.key].arn
  runtime       = "python3.12"
  architectures = ["arm64"]
  handler       = each.value.handler
  filename      = local.web_zip
  timeout       = 60
  memory_size   = 1769 # one full vCPU: startup (AWS clients) is CPU-bound; measured 4.6-5.5 s at 512

  environment {
    variables = merge(local.db_env, each.value.environment)
  }

  lifecycle {
    ignore_changes = [filename] # code is deploy_web.sh's job
  }

  depends_on = [aws_cloudwatch_log_group.web, aws_iam_role_policy.web]
  tags       = { Milestone = "M3b" }
}

# The web function: callable only through this CloudFront distribution (signed requests). Function
# URLs created since October 2025 need both statements.
resource "aws_lambda_function_url" "web" {
  function_name      = aws_lambda_function.web["web"].function_name
  authorization_type = "AWS_IAM"
}

resource "aws_lambda_permission" "cloudfront_invoke_url" {
  statement_id           = "AllowCloudFrontInvokeFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.web["web"].function_name
  principal              = "cloudfront.amazonaws.com"
  source_arn             = aws_cloudfront_distribution.site.arn
  function_url_auth_type = "AWS_IAM"
}

resource "aws_lambda_permission" "cloudfront_invoke" {
  statement_id  = "AllowCloudFrontInvokeFunction"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.web["web"].function_name
  principal     = "cloudfront.amazonaws.com"
  source_arn    = aws_cloudfront_distribution.site.arn
}

# The webhook: Stripe calls it directly, so its URL is public (NONE) and its lock is Stripe's
# signature, checked over the raw body before anything is parsed.
resource "aws_lambda_function_url" "webhook" {
  function_name      = aws_lambda_function.web["webhook"].function_name
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "public_invoke_webhook_url" {
  statement_id           = "AllowPublicInvokeFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.web["webhook"].function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

# The second statement AWS requires since October 2025, limited to calls through the function URL:
# nobody can call the function directly with the Lambda API.
resource "aws_lambda_permission" "public_invoke_webhook" {
  statement_id             = "AllowPublicInvokeFunction"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.web["webhook"].function_name
  principal                = "*"
  invoked_via_function_url = true
}
