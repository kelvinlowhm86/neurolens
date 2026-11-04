# Permissions for the `neurolens-deploy` IAM user

The deploy user can only touch NeuroLens resources, so it cannot see or change anything else in the
AWS account (other projects' buckets or machines, for example). Five policy files:

| File | Attached to | What it allows |
|---|---|---|
| `neurolens-deploy-services.json` | the deploy user | S3 buckets and SQS queues named `neurolens-*`; alarms (and their history) and email topics named `neurolens-*`; Lambda functions, EventBridge rules and Lambda log groups named `neurolens-*` and the queue trigger of a `neurolens-*` function; the SageMaker endpoint, endpoint configurations and models named `neurolens-*`; the `neurolens-*` ECR repository and CodeBuild project; the Cloud Control actions the awscc provider uses for the endpoint configuration; the `neurolens-*` Aurora database, its RDS-created password secret and the Data API; Parameter Store under `/neurolens/` |
| `neurolens-deploy-compute.json` | the deploy user | VPC, subnets and security groups, all tagged `Project=neurolens`; roles named `neurolens-*`; handing roles only to SageMaker, CodeBuild, Lambda (and only inside the boundary, below); service-linked roles for SageMaker, CodeBuild, SageMaker endpoint scaling and RDS; Application Auto Scaling only on scaling targets tagged `Project=neurolens` (their ARNs carry a random id, so a name cannot scope them). It cannot launch EC2 instances. Decoding an access-denied error (read-only). Reading GPU spare-capacity scores and service quotas in any region (read-only, for choosing a region). Reading the account's CloudTrail event history (read-only; it cannot be scoped to NeuroLens, so it also shows other projects' events). |
| `neurolens-deploy-web.json` | the deploy user | M3b's website: Cognito user pools and CloudFront distributions may be created only tagged `Project=neurolens` and changed only when so tagged; CloudFront's origin signing settings and AWS's managed cache policies (untaggable, harmless alone); reading the account's Lambda limits |
| `neurolens-quota-requests.json` | the deploy user | Asking AWS for higher SageMaker and Lambda quotas in us-east-1, and seeing those requests' status; on the first request, creating the AWS helper role through which Service Quotas opens the support case. A quota is only a ceiling and costs nothing; the other policies still limit what may run |
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
SageMaker and CodeBuild logs, and delete logins only from a Cognito pool tagged `Project=neurolens` (no pool ID, so the same files work in any account; the deploy user cannot tag another project's pool). No role may end a
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

Four steps, done once, signed in as the account owner (or an admin). IAM and Billing are the same
in every region; only step 3 needs the region set to **US East (N. Virginia) us-east-1** (top right
of the console).

1. **Access key for the deployer's user** (it already exists): IAM, Users, the user, Security
   credentials, Create access key, "Command Line Interface (CLI)". Send both parts (the key ID and
   the secret) to the deployer privately, for example a password-manager share; not in a group chat.
   The secret is shown only once.
2. **Permission policies:** follow "Apply" above (create the five policies, attach four to the user).
3. **Bedrock** (lets the app write reports with Claude): region us-east-1, Bedrock console, Model
   catalog, open **Claude Sonnet 4.6** (Anthropic). If it asks for use-case details, fill in the
   short form (a university course project that writes a text summary of a video analysis). Then
   open it in the Playground and send one short message ("hi"): the first use switches the model
   on for the account, which the app's own limited permissions cannot do. Costs a fraction of a cent.
4. **Credits:** Billing, Credits: check each credit's applicable products include SageMaker,
   Bedrock, Lambda, Aurora (RDS), S3 and CloudFront, so nothing is charged to the card.

At the end of the project: delete the user and the five `neurolens-*` policies.

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
- The policies are managed policies: each stays under IAM's 6,144-character limit (without
  whitespace). Adding many more actions needs another policy file.
