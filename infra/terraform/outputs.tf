# Put these in the git-ignored .env at the repo root (bucket_name -> NEUROLENS_S3_BUCKET,
# queue_url -> NEUROLENS_SQS_QUEUE_URL).

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
