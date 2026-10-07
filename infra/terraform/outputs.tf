# Put these in the git-ignored .env at the repo root (bucket_name -> NEUROLENS_S3_BUCKET,
# queue_url -> NEUROLENS_SQS_QUEUE_URL, db_cluster_arn / db_secret_arn / db_name ->
# NEUROLENS_DB_CLUSTER_ARN / NEUROLENS_DB_SECRET_ARN / NEUROLENS_DB_NAME).

output "bucket_name" {
  value = aws_s3_bucket.main.bucket
}

output "queue_url" {
  value = aws_sqs_queue.jobs.url
}

output "queue_arn" {
  value = aws_sqs_queue.jobs.arn
}

output "dlq_url" {
  value = aws_sqs_queue.jobs_dlq.url
}

output "public_subnet_id" {
  value = aws_subnet.public.id
}

output "build_subnet_ids" {
  description = "Public subnets infra/build_ami.sh tries in order, one per zone (space-separated)."
  value       = join(" ", concat([aws_subnet.public.id], aws_subnet.public_build[*].id))
}

output "no_inbound_security_group_id" {
  value = aws_security_group.no_inbound.id
}

output "build_instance_profile" {
  value = aws_iam_instance_profile.build.name
}

output "nat_instance_id" {
  value = aws_instance.nat.id
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
  value = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.users.id}"
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
