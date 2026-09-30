# Copy these into the "aws" block of config.json (bucket -> s3_bucket, queue_url -> sqs_queue_url).

output "bucket_name" {
  value = aws_s3_bucket.main.bucket
}

output "queue_url" {
  value = aws_sqs_queue.jobs.url
}

output "queue_arn" {
  value = aws_sqs_queue.jobs.arn
}
