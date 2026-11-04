# Letting Josh deploy NeuroLens in your AWS account

This guide gives Josh his own login (an AWS "user") inside your account, allowed to set up NeuroLens
and nothing else. Do it once, about 20 minutes, signed in as the account owner (root) or an admin.
Only step 4's test message costs money, a fraction of a cent.

**Safe to know.** Josh's user can only create or change things named `neurolens-...`. It cannot
open or change anything else in the account, start ordinary servers, or change billing. It can read
a few account-wide lists: service limits, GPU availability, and the activity log, which also lists
other projects' events. You can remove his access
at any time by deleting his access key (step 3's Security credentials page).

## 1. Create the five policies

A **policy** is a list of what a user or a machine may do. Open
https://us-east-1.console.aws.amazon.com/iam/home#/policies/create in your browser.

For each file in the table below:

1. Click the file link. The page shows a block of text.
2. Select all of it (Cmd+A on a Mac, Ctrl+A on Windows) and copy.
3. Back in the Create policy page, click **JSON** (top right of the editor box).
4. Select all the text already in the editor and paste over it.
5. Click **Next**. Under **Policy name**, type the name from the table exactly.
6. Click **Create policy**.

| File to open | Name to type |
|---|---|
| https://raw.githubusercontent.com/kelvinlowhm86/neurolens/aws-josh/infra/iam/neurolens-role-boundary.json | `neurolens-role-boundary` |
| https://raw.githubusercontent.com/kelvinlowhm86/neurolens/aws-josh/infra/iam/neurolens-deploy-services.json | `neurolens-deploy-services` |
| https://raw.githubusercontent.com/kelvinlowhm86/neurolens/aws-josh/infra/iam/neurolens-deploy-compute.json | `neurolens-deploy-compute` |
| https://raw.githubusercontent.com/kelvinlowhm86/neurolens/aws-josh/infra/iam/neurolens-deploy-web.json | `neurolens-deploy-web` |
| https://raw.githubusercontent.com/kelvinlowhm86/neurolens/aws-josh/infra/iam/neurolens-quota-requests.json | `neurolens-quota-requests` |

Tip: if a red error appears after pasting, the copy missed part of the text. Copy it again.

## 2. Give Josh's user four of them

Open https://us-east-1.console.aws.amazon.com/iam/home#/users and click the user **Josh**.

1. Open the **Permissions** tab.
2. Click **Add permissions**, then **Add permissions** again.
3. Choose **Attach policies directly**.
4. In the search box, type `neurolens`.
5. Tick these four: `neurolens-deploy-services`, `neurolens-deploy-compute`,
   `neurolens-deploy-web`, `neurolens-quota-requests`.
6. Click **Next**, then **Add permissions**.

Do **not** tick `neurolens-role-boundary`. It is a ceiling for the machines Josh's setup creates,
never attached to a user.

Check: the Permissions tab now lists exactly those four and nothing broad such as
AdministratorAccess.

## 3. Make an access key for Josh

Still on Josh's page (Users, Josh):

1. Open the **Security credentials** tab.
2. Under **Access keys**, click **Create access key**.
3. Choose **Command Line Interface (CLI)**, tick the confirmation box, click **Next**, then
   **Create access key**.
4. Send Josh both the **Access key ID** and the **Secret access key** privately: a password-manager
   share, or a direct message you can delete, never a group chat. The secret is shown only once.
   **Download .csv file** keeps a copy; delete it after sending.

## 4. Turn on Claude (Bedrock)

The app uses Claude Sonnet 4.6, through AWS's Bedrock service, to write the AI report on each
analysed video. This step lets it do that.

Check the region at the top right says **United States (N. Virginia)**; if not, pick it. Open
https://us-east-1.console.aws.amazon.com/bedrock/home?region=us-east-1#/model-catalog, search
`Claude Sonnet 4.6` (made by Anthropic) and open it.

If it asks for use-case details, fill the short form: company/use = university course project
(NUS); use case = writes a short text summary of a video analysis, for course testers and visitors
to a course exhibition.

Then click **Open in playground** (or Playground, then Chat, and pick Claude Sonnet 4.6), type
`hi`, and send. Why: the first message switches the model on for the whole account, and Josh's
limited user is not allowed to do that switch.

## 5. Check the credits cover it

Open https://us-east-1.console.aws.amazon.com/billing/home#/credits. For each credit, open its
details and check the applicable products include SageMaker, Bedrock, Lambda, RDS (Aurora), S3 and
CloudFront.

Tell Josh the remaining amount and expiry date. If one of those six is missing, tell him before he
deploys: that usage would be charged to your card.

## 6. Done

Send Josh the access key privately, and tell him the credit amount and expiry date (and anything
from step 5 that isn't covered).

## 7. When the course ends

Once NeuroLens has been taken down, delete the user **Josh** (IAM, Users) and the five `neurolens-`
policies (IAM, Policies, search `neurolens`).
