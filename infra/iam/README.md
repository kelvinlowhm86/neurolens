# Permissions for the `neurolens-deploy` IAM user

The deploy user can only touch NeuroLens resources, so it cannot see or change anything else in the
AWS account (other projects' buckets or machines, for example). Five policy files:

| File | Attached to | What it allows |
|---|---|---|
| `neurolens-deploy-services.json` | the deploy user | S3 buckets and SQS queues named `neurolens-*`; alarms (and their history) and email topics named `neurolens-*`; Lambda functions, EventBridge rules and Lambda log groups named `neurolens-*` and the queue trigger of a `neurolens-*` function; the SageMaker endpoint, endpoint configurations and models named `neurolens-*`; the `neurolens-*` ECR repository and CodeBuild project; the Cloud Control actions the awscc provider uses for the endpoint configuration; the `neurolens-*` Aurora database, its RDS-created password secret and the Data API; Parameter Store under `/neurolens/` |
| `neurolens-deploy-compute.json` | the deploy user | VPC, subnets and security groups, all tagged `Project=neurolens`; roles named `neurolens-*`; handing roles only to SageMaker, CodeBuild, Lambda (and only inside the boundary, below); service-linked roles for SageMaker, CodeBuild, SageMaker endpoint scaling and RDS; Application Auto Scaling only on scaling targets tagged `Project=neurolens` (their ARNs carry a random id, so a name cannot scope them). It cannot launch EC2 instances. Decoding an access-denied error (read-only). Reading GPU spare-capacity scores and service quotas in any region (read-only, for choosing a region). Reading the account's CloudTrail event history (read-only; it cannot be scoped to NeuroLens, so it also shows other projects' events). |
| `neurolens-deploy-web.json` | the deploy user | M3b's website: Cognito user pools and CloudFront distributions may be created only tagged `Project=neurolens` and changed only when so tagged; CloudFront's origin signing settings and AWS's managed cache policies (untaggable, harmless alone); reading the account's Lambda limits |
| `neurolens-quota-requests.json` | the deploy user | Asking AWS for higher SageMaker and Lambda quotas in us-east-1, and seeing those requests' status. A quota is only a ceiling and costs nothing; the other policies still limit what may run |
| `neurolens-role-boundary.json` | nobody directly (a *permissions boundary*) | The most any `neurolens-*` role may ever do, whatever is written into it |

**Why the boundary.** The deploy user creates the roles that machines and Lambdas use. Without a
limit, someone holding the deploy user's keys could create a `neurolens-` role with full account
access, start a machine with it, and escape every other restriction here. A permissions boundary is
a ceiling AWS checks on top of a role's own policy: the compute policy only lets the deploy user
create or edit roles that carry this boundary, and the deploy user cannot edit the boundary itself.
So a role can never do more than read and write NeuroLens storage and queues, use the NeuroLens
database through the Data API, read `/neurolens/` parameters, pull and push the worker image,
resize only the NeuroLens endpoint, start only the report function, call only Claude Sonnet 4.6 on
Bedrock through its US inference profile, publish to NeuroLens SNS topics, publish SageMaker endpoint instance metrics, write its own Lambda,
SageMaker and CodeBuild logs, and delete logins from our Cognito pool. No role may end a
machine or change the endpoint's scaling target.

Terraform sets `permissions_boundary` on every role it creates, or AWS refuses the call.

## Apply (by hand in the console; the deploy user has no rights on its own permissions, on purpose)

1. IAM, Policies, Create policy, JSON tab: paste `neurolens-role-boundary.json`, name it exactly
   `neurolens-role-boundary`. Do not attach it to anything.
2. Same again for `neurolens-deploy-services.json` (name `neurolens-deploy-services`),
   `neurolens-deploy-compute.json` (name `neurolens-deploy-compute`), `neurolens-deploy-web.json`
   (name `neurolens-deploy-web`) and `neurolens-quota-requests.json` (name `neurolens-quota-requests`).
3. IAM, Users, `neurolens-deploy` (or whatever the deploy user is called), Permissions, Add
   permissions, Attach policies directly: tick `neurolens-deploy-services`, `neurolens-deploy-compute`,
   `neurolens-deploy-web` and `neurolens-quota-requests`.
4. Only then remove any other policy on the user (the old inline `neurolens-deploy-scoped`, or a broad
   one such as AdministratorAccess), so it can touch NeuroLens only.

## New account: what the account owner does

Done once, signed in as the account owner (or an admin), in region **us-east-1 (N. Virginia)**.
Everything else the deploy user does itself.

1. Create the IAM user if it does not exist yet (IAM, Users, Create user; no console access needed).
   Create an access key for it (Security credentials, Create access key, "Command Line Interface")
   and send the key to the deployer privately (not by email or chat in plain text; a password
   manager share or similar). Never put it in the repo.
2. Apply the five policies: the "Apply" steps above.
3. Bedrock, so the app can write reports with Claude: Bedrock console, Model catalog, open
   **Claude Sonnet 4.6** (Anthropic). If it asks for use-case details, fill in the short form
   (use case: a university course project that writes a text summary of a video analysis) and submit.
   It is free; approval is usually quick.
4. Check the account's credits cover SageMaker, Bedrock, Lambda, Aurora, S3 and CloudFront
   (Billing, Credits, each credit's "Applicable products"), so nothing is charged to the card.
5. Later, once: the deployer sends an updated `neurolens-role-boundary.json` (it names the new
   account's sign-in pool, which exists only after the first deploy). Open the policy
   `neurolens-role-boundary`, Edit, paste it, Save.

The GPU quota (SageMaker "ml.g6e.xlarge for endpoint usage" and its backups) and any Lambda limit
increase are requested by the deployer with the quota policy above.

Edits later: open the policy, Edit, paste the new file. AWS keeps up to five versions.

## Check (with the `neurolens` profile)

- `aws s3api head-bucket --bucket neurolens-tfstate-<account-id>` works, and
  `aws s3api list-buckets` is refused.
- `aws ec2 describe-vpcs --query 'Vpcs[].VpcId'` works (reading is allowed everywhere).
- `aws iam get-role --role-name neurolens-does-not-exist` says `NoSuchEntity`, not `AccessDenied`.
- The real check is `terraform plan` and `apply` running without `AccessDenied`.

## Known limitations (accepted)

- Application Auto Scaling's `DescribeScalableTargets` and Cloud Control's operations cannot be
  limited to one resource, so those read/Cloud-Control actions are account-wide. The write actions
  (registering targets, policies, scheduled actions) are limited to the `neurolens` endpoint.
- The boundary names our Cognito user pool by its ID (`DeleteCognitoLoginsInOurPool`), so deleting
  an account can never remove logins from another project's pool. If the pool is ever recreated,
  put the new ID there and re-apply; until then account deletion fails to remove logins and the
  `COGNITO_DELETE_FAILED` alarm emails.
- The policies are managed policies: each stays under IAM's 6,144-character limit (without
  whitespace). Adding many more actions needs another policy file.
