# The public website's front door and sign-in (M3b §2a, §3b). Day one builds the CloudFront
# address with the page origin and Cognito with Google, so the sign-in round trip can be checked
# before any web code exists; the Lambda API origin is added with the web function.

# ─── CloudFront: one HTTPS address for the page (and later the API) ───────

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "neurolens-site"
  description                       = "CloudFront signs its requests to the bucket's site/ prefix (the bucket stays private)."
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
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
