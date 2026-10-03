# Permissions for the `neurolens-deploy` IAM user

The deploy user can only touch NeuroLens resources, so it cannot see or change anything else in the AWS
account (other projects' buckets or machines, for example). Three policy files:

| File | Attached to | What it allows |
|---|---|---|
| `neurolens-deploy-services.json` | the deploy user | S3 buckets and SQS queues named `neurolens-*`; Auto Scaling groups, alarms and email topics named `neurolens-*`; Parameter Store under `/neurolens/`; remote commands and sessions only on machines tagged `Project=neurolens` |
| `neurolens-deploy-compute.json` | the deploy user | EC2 and networking: anything may be *read*; new things may be created only if tagged `Project=neurolens`; only things already tagged `Project=neurolens` may be changed, stopped or deleted. Machines only of the types the project uses (`g6e.xlarge`, `g6e.2xlarge`; `g6.2xlarge` and `g5.2xlarge` for the GPU benchmark; `t3.large`, `t4g.micro`). Roles and instance profiles named `neurolens-*`, handed only to EC2 |
| `neurolens-role-boundary.json` | nobody directly (a *permissions boundary*) | The most any `neurolens-*` role may ever do, whatever is written into it |

**Why the boundary.** The deploy user creates the roles that machines use. Without a limit, someone holding
the deploy user's keys could create a `neurolens-` role with full account access, start a machine with it,
and so escape every other restriction here. A permissions boundary is a ceiling AWS checks on top of a role's
own policy: the compute policy only lets the deploy user create or edit roles that carry this boundary, and
the deploy user cannot edit the boundary itself. So a role can never do more than read and write NeuroLens
storage and queues, read `/neurolens/` parameters, end its own machine, and talk to Session Manager.
Terraform must set `permissions_boundary` on every role it creates, or AWS refuses the call.

## Apply (by hand in the console; the deploy user has no rights on its own permissions, on purpose)

1. IAM, Policies, Create policy, JSON tab: paste `neurolens-role-boundary.json`, name it exactly
   `neurolens-role-boundary`. Do not attach it to anything.
2. Same again for `neurolens-deploy-services.json` (name `neurolens-deploy-services`) and
   `neurolens-deploy-compute.json` (name `neurolens-deploy-compute`).
3. IAM, Users, `neurolens-deploy`, Permissions, Add permissions, Attach policies directly: tick
   `neurolens-deploy-services` and `neurolens-deploy-compute`.
4. Only then remove the old inline policy `neurolens-deploy-scoped` (its two statements are now in
   `neurolens-deploy-services`). Inline policies are capped at 2,048 characters per user, which is why these
   are managed policies now.

Edits later: open the policy, Edit, paste the new file. AWS keeps up to five versions.

## Check (with the `neurolens` profile)

- `aws s3api head-bucket --bucket neurolens-tfstate-<account-id>` works, and `aws s3api list-buckets` is refused.
- `aws ec2 describe-vpcs --query 'Vpcs[].VpcId'` works (reading is allowed everywhere).
- `aws iam get-role --role-name neurolens-does-not-exist` says `NoSuchEntity`, not `AccessDenied`.
- The real check is `terraform plan` and `apply` in the next step running without `AccessDenied`.

## Known limitations (accepted)

- Machines launched by the Auto Scaling group are started by AWS's own Auto Scaling role, so the instance-type
  list above does not apply to them; the Launch Template (in Terraform) fixes their type instead. Someone with
  the deploy keys could still run up a bill this way; the budget alerts and the GPU quota are the backstop.
- A `neurolens-` machine's type can be changed while it is stopped. Same backstop.
- Later milestones need more (M2b metrics, M3 database and secrets): widen the boundary and the deploy
  policies then, by the same steps.
