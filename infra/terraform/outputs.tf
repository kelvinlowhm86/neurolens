# Put these in the git-ignored .env at the repo root (bucket_name -> NEUROLENS_S3_BUCKET,
# gpu_endpoint -> NEUROLENS_GPU_ENDPOINT,
# db_cluster_arn / db_secret_arn / db_name ->
# NEUROLENS_DB_CLUSTER_ARN / NEUROLENS_DB_SECRET_ARN / NEUROLENS_DB_NAME).

output "bucket_name" {
  value = aws_s3_bucket.main.bucket
}

output "gpu_endpoint" {
  description = "The SageMaker endpoint's name (the web app and the GPU scripts use it)."
  value       = local.endpoint_name
}

output "worker_ecr_repository" {
  description = "Where infra/build_image.sh pushes the SageMaker image."
  value       = aws_ecr_repository.worker.repository_url
}

output "worker_image_build_project" {
  description = "The CodeBuild project infra/build_image.sh starts."
  value       = aws_codebuild_project.worker_image.name
}

output "db_cluster_arn" {
  value = aws_rds_cluster.db.arn
}

output "db_secret_arn" {
  description = "The ARN of the database's password secret (not the password itself)."
  value       = local.db_secret_arn
}

output "db_name" {
  value = local.db_name
}

output "region" {
  description = "The region everything runs in (M2a §4i); the scripts in infra/ read it from here."
  value       = var.region
}

output "public_base_url" {
  description = "The website's HTTPS address (CloudFront's free *.cloudfront.net name)."
  value       = local.public_base_url
}

output "cognito_issuer" {
  value = local.cognito_issuer
}

output "cognito_client_id" {
  value = aws_cognito_user_pool_client.web.id
}

output "cognito_domain" {
  value = local.cognito_domain
}

output "google_redirect_uri" {
  description = "Paste into the Google OAuth client's authorized redirect URIs."
  value       = "https://${local.cognito_domain}/oauth2/idpresponse"
}

output "sign_in_test_url" {
  description = "Day-one check (M3b): Cognito's sign-in page; a successful sign-in lands on <public_base_url>/auth/callback?code=..."
  value       = "https://${local.cognito_domain}/oauth2/authorize?client_id=${aws_cognito_user_pool_client.web.id}&response_type=code&scope=openid+email&redirect_uri=${local.public_base_url}/auth/callback"
}

output "cloudfront_distribution_id" {
  description = "Read by infra/deploy_web.sh to clear the cache after a deploy."
  value       = aws_cloudfront_distribution.site.id
}
