# Permissions for the `neurolens-deploy` IAM user

`neurolens-deploy-policy.json` limits that user to S3 buckets and SQS queues whose names start with
`neurolens-`, so it cannot see or change anything else in the AWS account (other projects' buckets, for
example). It is the M1 version; M2a adds statements for EC2, networking and roles.

Apply it by hand in the AWS console (the deploy user has no IAM permissions, on purpose):

1. IAM, Users, `neurolens-deploy`, Permissions.
2. Remove `AmazonS3FullAccess` and `AmazonSQSFullAccess`.
3. Add permissions, Create inline policy, JSON tab, paste the file's contents, name it
   `neurolens-deploy-scoped`, create.

Then check, using the `neurolens` profile: `aws s3api head-bucket --bucket neurolens-tfstate-<account-id>`
works, and `aws s3api list-buckets` is refused (that call is deliberately not allowed).
