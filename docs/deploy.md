# Production deployment runbook

Hosts the ESL Pricing Service on AWS (App Runner, RDS Postgres, Cognito,
Fargate one-off tasks, Terraform in `infra/`). Run all commands from the repo
root in PowerShell.

Steps marked **[CHANGES AWS]**, **[CHANGES DNS]**, **[CHANGES GITHUB]** or
**[CHANGES SQUARE]** modify real systems. Confirm each before running it.
Secret values are typed by you in your own terminal; never paste them into
chat, commits or logs.

## Before you start

Decisions to settle first. Record the answers in `infra/prod/prod.tfvars`
(git-ignored; start from `infra/prod/prod.tfvars.example`).

| # | Decision | Value |
|---|---|---|
| 1 | Domain: an existing Route 53 public hosted zone (if the domain is registered elsewhere, create the hosted zone and delegate its NS records at the registrar first) | `levstra.com` (`hosted_zone_name`) |
| 2 | App subdomain | `app` (app host `app.levstra.com`, URL `https://app.levstra.com`) |
| 3 | Cognito domain prefix (globally unique) | _to decide_ (`cognito_domain_prefix`) |
| 4 | Alarm email address | _to decide_ (`alarm_email`) |
| 5 | Region | `us-west-2` |
| 5b | Does the AWS account already have a GitHub OIDC provider? If yes, set `create_github_oidc_provider = false` | _to decide_ |
| 6 | Square **production** application ID (the secret is entered later, by you) | _to decide_ (`square_application_id`) |

## Preflight (read-only)

```powershell
aws sts get-caller-identity
aws route53 list-hosted-zones-by-name --dns-name levstra.com --max-items 1
aws iam list-open-id-connect-providers
```

The account must be the one you intend. The `levstra.com` hosted zone must
exist before `deploy_service = true`. If a GitHub OIDC provider
(`token.actions.githubusercontent.com`) is already listed, set
`create_github_oidc_provider = false`; otherwise leave it `true`.

## 1. State bucket [CHANGES AWS]

```powershell
$account = aws sts get-caller-identity --query Account --output text
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap apply -var "state_bucket_name=esl-tfstate-$account"
Copy-Item infra/prod/backend.hcl.example infra/prod/backend.hcl   # then set bucket = "esl-tfstate-<account>"
terraform -chdir=infra/prod init -reconfigure -backend-config=backend.hcl
```

Expected: bucket created; `init` reports `Successfully configured the backend "s3"!`.

## 2. Base infrastructure, no service yet [CHANGES AWS]

Review the plan summary before applying.

```powershell
terraform -chdir=infra/prod plan -var-file=prod.tfvars -out=base.tfplan
terraform -chdir=infra/prod apply base.tfplan
```

Expected: VPC, NAT, RDS (~10 min), secrets, ECR, Cognito, ECS and the OIDC
role are created; the `apprunner_service_arn` output is empty. The SNS
confirmation email arrives: click "Confirm subscription".

## 3. Set the secret values [CHANGES AWS]

You run these; nobody else needs to see the values.

```powershell
# Token encryption key (Fernet). Generate and store in one go so it never hits the screen:
$key = .\.venv\Scripts\python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
aws secretsmanager put-secret-value --secret-id (terraform -chdir=infra/prod output -json secret_arns | ConvertFrom-Json).token_encryption_key --secret-string $key
Remove-Variable key
# Square production application secret (Square Developer Console -> production app -> Credentials):
aws secretsmanager put-secret-value --secret-id <secret_arns.square_application_secret> --secret-string '<paste>'
# Webhook signature key: placeholder now, real value in step 8.
aws secretsmanager put-secret-value --secret-id <secret_arns.square_webhook_signature_key> --secret-string 'pending'
```

## 4. First image [CHANGES AWS]

Build from a clean checkout of the commit being deployed, not a working tree
with uncommitted edits (e.g. `git worktree add ..\esl-release <sha>`, then run
this step from there).

```powershell
$repo = terraform -chdir=infra/prod output -raw ecr_repository_url
aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin $repo.Split('/')[0]
docker build --platform linux/amd64 `
  --build-arg VITE_COGNITO_AUTHORITY=(terraform -chdir=infra/prod output -raw cognito_authority) `
  --build-arg VITE_COGNITO_CLIENT_ID=(terraform -chdir=infra/prod output -raw cognito_app_client_id) `
  --build-arg VITE_COGNITO_DOMAIN=(terraform -chdir=infra/prod output -raw cognito_domain_url) `
  -t "${repo}:prod" -t "${repo}:candidate" .
docker push "${repo}:prod"; docker push "${repo}:candidate"
```

## 5. Database roles, then migrations [CHANGES AWS]

```powershell
.\scripts\admin.ps1 -Task bootstrap
.\scripts\admin.ps1 -Task migrate
```

Expected: bootstrap prints `Database roles ready: esl_admin owns esl; esl_app can log in.`
and `Exit code: 0`; migrate prints Alembic's `Running upgrade ... -> 0007` lines
and `Exit code: 0`.

## 6. Create the service and domain [CHANGES AWS] [CHANGES DNS]

Set `deploy_service = true` in `prod.tfvars` first.

```powershell
terraform -chdir=infra/prod apply -var-file=prod.tfvars -target=aws_apprunner_service.app -target=aws_apprunner_custom_domain_association.app
terraform -chdir=infra/prod apply -var-file=prod.tfvars
aws apprunner describe-custom-domains --service-arn (terraform -chdir=infra/prod output -raw apprunner_service_arn) --query 'CustomDomains[0].Status'
```

Expected: service `RUNNING`; custom-domain status moves
`pending_certificate_dns_validation` to `active` (up to ~30 min after the
validation records exist). If the second apply fails on the log metric filter
because App Runner has not created the log group yet, wait a minute and re-run it.

## 7. Smoke test production

```powershell
$url = terraform -chdir=infra/prod output -raw app_url
curl.exe -s "$url/v1/health"
curl.exe -s -o NUL -w "%{http_code} %{content_type}`n" "$url/"
curl.exe -s -o NUL -w "%{http_code}`n" "$url/v1/tags"
```

Expected: `{"status":"ok"}`; `200 text/html; charset=utf-8`; `401` (signed out).
App Runner application logs show no startup-guard error (the app connected as `esl_app`).

## 8. Square production registration [CHANGES SQUARE] [CHANGES AWS]

In the Square Developer Console, production tab:

1. OAuth redirect URL: `https://app.levstra.com/v1/square/oauth/callback`.
2. Webhook subscription: URL `https://app.levstra.com/v1/webhooks/square`,
   events `catalog.version.updated` and `oauth.authorization.revoked`, API
   version matching `square_api_version` in `src/esl/config.py`.
3. Store the subscription's signature key:
   `aws secretsmanager put-secret-value --secret-id <secret_arns.square_webhook_signature_key> --secret-string '<paste>'`
4. Redeploy so App Runner reads the new secret values:
   `aws apprunner start-deployment --service-arn <apprunner_service_arn>`

## 9. GitHub environment and variables [CHANGES GITHUB]

Uses the `gh` CLI. Merging to `main` before this step produces one red
`deploy` run (no role or variables yet); that is expected and harmless.

```powershell
# Restrict the environment to main; the OIDC trust on environment:production then only works from main.
gh api -X PUT "repos/levstra/SquareRetailTags/environments/production" -F "deployment_branch_policy[protected_branches]=false" -F "deployment_branch_policy[custom_branch_policies]=true" | Out-Null
gh api -X POST "repos/levstra/SquareRetailTags/environments/production/deployment-branch-policies" -f name=main | Out-Null
$o = terraform -chdir=infra/prod output -json | ConvertFrom-Json
$vars = @{
  AWS_REGION = $o.region.value
  AWS_DEPLOY_ROLE_ARN = $o.github_deploy_role_arn.value
  ECR_REPOSITORY_URL = $o.ecr_repository_url.value
  ECS_CLUSTER = $o.ecs_cluster_name.value
  PRIVATE_SUBNET_IDS = ($o.private_subnet_ids.value -join ',')
  TASK_SECURITY_GROUP_ID = $o.task_security_group_id.value
  MIGRATE_TASK_DEFINITION = $o.task_definitions.value.migrate
  TASKS_LOG_GROUP = $o.tasks_log_group.value
  APPRUNNER_SERVICE_ARN = $o.apprunner_service_arn.value
  APP_URL = $o.app_url.value
  VITE_COGNITO_AUTHORITY = $o.cognito_authority.value
  VITE_COGNITO_CLIENT_ID = $o.cognito_app_client_id.value
  VITE_COGNITO_DOMAIN = $o.cognito_domain_url.value
}
foreach ($k in $vars.Keys) { gh variable set $k --env production --body $vars[$k] }
gh variable list --env production
```

Expected: 13 variables listed.

## 10. First client business [CHANGES AWS]

```powershell
.\scripts\admin.ps1 create-business --name "<business name>" --owner-email <owner email>
```

Expected: `Created business <uuid>; invited <email> as owner.` and `Exit code: 0`.
The owner receives Cognito's invite email, signs in at `https://app.levstra.com`,
sets a password, and connects Square (Square tab, Connect Square, approve,
choose locations, Save stores). Confirm the catalog appears.

## 11. Pipeline end to end

Once these changes reach `main`, confirm the `deploy` workflow run is green:
tests, build, migration exit 0, deployment `SUCCEEDED`, smoke test 200.

## 12. Alarm check

```powershell
aws cloudwatch describe-alarms --alarm-name-prefix esl-prod --query 'MetricAlarms[].[AlarmName,StateValue]' --output table
```

Expected: `esl-prod-5xx`, `esl-prod-square-sync-failures`,
`esl-prod-label-repends` and `esl-prod-nat-recover` present; states `OK` or `INSUFFICIENT_DATA`.

## Routine deploys

Merge to `main`. The `deploy` workflow tests, pushes `:<sha>`, runs migrations
from `:candidate`, promotes `:prod`, and waits for App Runner. Migrations must
be backward compatible with the release that is still running (expand first,
contract in a later release).

Releases that add a synced column (e.g. `catalog_variations.gtin`, migration
`0008`) only populate it for objects Square returns in a sync after the release.

- A database created at or after `0008`: nothing to do. A business's first sync
  is a full sync, so the column is filled from the start.
- An existing database: after the new release is live, force one full sync per
  business by deleting its delta cursor. There is no `esl admin` subcommand for
  this; run the SQL in a session connected as `esl_admin` (the RDS instance is
  private, so from inside the VPC):

  ```sql
  DELETE FROM sync_cursors WHERE business_id = '<business uuid>';
  ```

  The next scheduled sync is then full. It rewrites no tags: the new column is
  not part of the projection, so content hashes are unchanged and nothing is
  enqueued. Do not do this inside the migration.

## Rollback

If a deploy run went red after the "Promote to :prod and deploy" step and CI
did not report restoring `:prod`, re-point `:prod` with the procedure below
before any `start-deployment` or `admin.ps1` run (they would use the rejected
image).

```powershell
$sha = '<previous good commit>'   # the full 40-character sha (CI tags images with $GITHUB_SHA)
$manifest = aws ecr batch-get-image --repository-name esl-prod --image-ids imageTag=$sha --query 'images[0].imageManifest' --output text
[System.IO.File]::WriteAllText("$PWD\manifest.json", ($manifest -join "`n"))   # not `>`: PowerShell 5.1 would write UTF-16
aws ecr put-image --repository-name esl-prod --image-tag prod --image-manifest file://manifest.json
aws apprunner start-deployment --service-arn <apprunner_service_arn>
```

Schema changes are not rolled back automatically; a release that needs a
schema rollback needs a new forward migration.

## Sign-in domain and the bare domain [CHANGES AWS] [CHANGES DNS] [CHANGES GITHUB]

With `deploy_service = true`, Terraform puts Cognito sign-in on
`login.levstra.com` (`login_subdomain`) and serves `levstra.com` from
CloudFront (`site.tf`). Cognito refuses a custom domain whose parent has no A
record, so the bare domain has to exist first; for now it 302-redirects to the
app. The `amazoncognito.com` prefix domain stays as a fallback.

Moving an existing deployment over:

```powershell
terraform -chdir=infra/prod apply -var-file=prod.tfvars
curl.exe -s -o NUL -w "%{http_code}`n" https://levstra.com/          # 302
curl.exe -s -o NUL -w "%{http_code}`n" "https://login.levstra.com/oauth2/authorize?client_id=$(terraform -chdir=infra/prod output -raw cognito_app_client_id)&response_type=code&scope=openid&redirect_uri=https://app.levstra.com/auth/callback"   # 200 or 302
gh variable set VITE_COGNITO_DOMAIN --env production --body (terraform -chdir=infra/prod output -raw cognito_domain_url)
```

The Cognito domain can take up to an hour to answer after the apply. Change the
GitHub variable only once the second `curl` stops failing, then merge
anything (or re-run the deploy workflow) so the frontend is rebuilt with it.
Rolling back is setting the variable back to the prefix-domain URL and
rebuilding.

Landing page later: upload it to the `esl-prod-site-<account>` bucket with
`index.html` at the root, and delete the `function_association` block in
`site.tf`.

## Email [CHANGES AWS] [CHANGES DNS]

Cognito's emails (invites, password resets, verification and sign-in codes)
can come from `Levstra <no-reply@levstra.com>`, Reply-To
`levstra@levstra.com`, sent through SES with DKIM, a `mail.levstra.com` MAIL
FROM and DMARC. The wording and layout come from the custom message Lambda,
`src/esl/accounts/cognito_emails.py` (preview: `python -m
esl.accounts.cognito_emails preview <dir>`). Everything is in
`infra/prod/email.tf`. The apex TXT record and the ForwardEmail MX records
were made by hand and are not touched.

### Stage 1: SES domain, DNS, Lambda (pool unchanged)

```powershell
terraform -chdir=infra/prod plan "-var-file=prod.tfvars"    # creates only; aws_cognito_user_pool.main not listed
terraform -chdir=infra/prod apply "-var-file=prod.tfvars"
aws sesv2 get-email-identity --email-identity levstra.com --query '{dkim:DkimAttributes.Status,mailFrom:MailFromAttributes.MailFromDomainStatus}'
curl.exe -sI https://levstra.com/email/wordmark.png         # 200, content-type: image/png
curl.exe -s -o NUL -w "%{http_code}`n" https://levstra.com/  # still 302
```

DKIM and MAIL FROM can take up to an hour to show `SUCCESS`.

### Stage 2: SES production access, dev test, mail-tester

New SES accounts are in the sandbox: 200 emails a day, and only to verified
addresses. Request production access in the console (SES -> Account dashboard
-> Request production access), mail type **Transactional**, website
`https://levstra.com`, and this use case:

> Transactional email only: account invitations, password reset codes and
> email verification codes for Levstra, an invite-only dashboard for retail
> electronic shelf labels with a few dozen users. No marketing or bulk mail.
> Every recipient is added deliberately by an owner or operator who invites
> them by email address; nobody can sign themselves up. Bounces and
> complaints are handled by the SES account-level suppression list, which is
> enabled. Mail is signed with DKIM on levstra.com, uses a custom MAIL FROM
> domain (mail.levstra.com) and is covered by a DMARC record.

While waiting, test on the dev pool (it verifies your address in the sandbox
first):

```powershell
.\scripts\dev_cognito_email.ps1 -UserPoolId <dev pool id> -RecipientEmail <you>
# "Forgot password" on http://localhost:5173, and a resent invite:
aws cognito-idp admin-create-user --user-pool-id <dev pool id> --username <you> --message-action RESEND
.\scripts\dev_cognito_email.ps1 -UserPoolId <dev pool id> -Disable   # drill: back to Cognito's default
aws cognito-idp describe-user-pool --user-pool-id <dev pool id> --query 'UserPool.LambdaConfig'   # no CustomMessage
```

Check in Gmail's "Show original": SPF, DKIM and DMARC `PASS`, `mailed-by:
mail.levstra.com`, `signed-by: levstra.com`. Check Apple Mail too, in light
and dark mode.

**Deliverability check (mail-tester).** Do this before stage 3. Re-enable the
dev pool, go to https://www.mail-tester.com, verify the address it shows in
SES the same way (`aws sesv2 create-email-identity --email-identity <that
address>`), and send a forgot-password email to it. Record the score here:

| Date | Score | Notes |
|---|---|---|
| | | |

**If the score is below 9/10, or the report flags the missing plain-text part
(`MIME_HTML_ONLY` or similar), open a follow-up to add a plain-text
version.** Cognito's custom message trigger can only send HTML, so that
follow-up means a Cognito *custom email sender* Lambda (approach C in
`docs/superpowers/specs/2026-10-28-cognito-email-design.md`) and needs its own
spec.

### Stage 3: switch the production pool (after SES approval)

Set `cognito_email_via_ses = true` in `prod.tfvars`, then:

```powershell
terraform -chdir=infra/prod plan "-var-file=prod.tfvars"    # aws_cognito_user_pool.main "updated in-place", never "must be replaced"
terraform -chdir=infra/prod apply "-var-file=prod.tfvars"
```

Then use "Forgot password" on https://app.levstra.com for your own account:
the email comes from `no-reply@levstra.com`, styled, with SPF/DKIM/DMARC
`PASS`.

### DMARC, about two weeks later

The DMARC reports go to `levstra@levstra.com`. When they show only aligned
mail from SES, set `dmarc_policy = "quarantine"` in `prod.tfvars` and apply.

### Rollback

Set `cognito_email_via_ses = false` and apply: the pool goes back to
Cognito's default sender and wording. The SES identity, DNS records and
Lambda stay and do nothing. Confirm the trigger is gone:

```powershell
aws cognito-idp describe-user-pool --user-pool-id (terraform -chdir=infra/prod output -raw cognito_user_pool_id) --query 'UserPool.LambdaConfig'
```

If `CustomMessage` is still set, Terraform didn't clear it. Clear it with
`aws cognito-idp update-user-pool`, passing the pool's other settings back
unchanged (as `scripts/dev_cognito_email.ps1 -Disable` does), and note it
here.

If the Lambda fails, Cognito sends nothing and the invite or reset fails;
the `esl-prod-cognito-emails-errors` alarm emails the alarm address.

### Before anything else sends as @levstra.com (invoicing)

1. Add the new sender's SPF include or DKIM records in Terraform. Keep a
   single SPF record per name.
2. Send a test to mail-tester and confirm SPF, DKIM and DMARC pass.
3. Only then send real mail. With DMARC at `quarantine`, unauthenticated mail
   from `levstra.com` goes to spam.

## Operator commands

`scripts/admin.ps1` runs `esl admin <subcommand>` as a one-off Fargate task in
production and prints its logs:

```powershell
.\scripts\admin.ps1 <esl admin subcommand> [args]
.\scripts\admin.ps1 -Task migrate      # alembic upgrade head (CI normally does this)
.\scripts\admin.ps1 -Task bootstrap    # one-time: create DB logins on a fresh RDS
```

Subcommands (from `src/esl/cli.py`):

```powershell
.\scripts\admin.ps1 create-business --name "<name>" --owner-email <email> [--store-name "Main store"]
.\scripts\admin.ps1 invite-user --business <uuid> --email <email> [--role owner|staff] [--all-stores]
.\scripts\admin.ps1 add-gateway --business <uuid> --store <uuid> --name "<pi name>"   # key printed once
.\scripts\admin.ps1 revoke-gateway <gateway-uuid>                                     # takes effect immediately
.\scripts\admin.ps1 set-store-code --store <uuid> --code <storeId the Pi uses>
.\scripts\admin.ps1 push-all --business <uuid> [--store <uuid>]
```

`--all-stores` applies to `--role staff` only. `bootstrap-db` also exists as
an `esl admin` subcommand but is run through `-Task bootstrap`. Run `push-all`
once after replacing a Pi (existing tags only get a command when their content
changes), and once after migrating an existing database to `0006`.

## Querying the database

RDS is private. `scripts/db.ps1` opens a Session Manager port forward through
the NAT instance and connects you (needs the AWS CLI plus the
[Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)):

```powershell
.\scripts\db.ps1          # esl_readonly: tunnel on localhost:15432, password on the clipboard
.\scripts\db.ps1 -Psql    # same login, in psql
.\scripts\db.ps1 -Admin   # esl_admin, for the rare write; asks you to type WRITE
```

The tunnel closes when you press Enter, leave psql, or press Ctrl+C. Point a
client (Rider, DBeaver) at `localhost:15432`, database `esl`, SSL `require`.
Port 15432 rather than 5433 so it can't be confused with the local docker
Postgres.

`esl_readonly` reads every business (a `readonly_select` RLS policy on every
table, migration 0009) and has only `SELECT`. Its sessions are read-only, and
it has a 30 s statement timeout and a 60 s idle-in-transaction timeout, so a
heavy query or a forgotten transaction can't block the app or a migration.
`esl_app` is no use here: without a business context it sees no rows.

One-time setup on an existing database: after the release with migration
0009 is live, `terraform apply` (creates the `esl-prod/db/esl-readonly-password`
secret and passes it to the bootstrap task), then `.\scripts\admin.ps1 -Task bootstrap`.
In production 0009 can't create the role (esl_admin is NOCREATEROLE) and
does nothing; bootstrap creates the login and applies the same grants.

## Rotating secrets

- Square / token key: `put-secret-value`, then `aws apprunner start-deployment`.
  Rotating `TOKEN_ENCRYPTION_KEY` makes stored Square tokens unreadable: every
  owner must reconnect Square. Don't rotate it without a re-encryption plan.
- DB passwords: `terraform -chdir=infra/prod apply -var-file=prod.tfvars -replace=random_password.db_app`
  (or `db_admin`), then `.\scripts\admin.ps1 -Task bootstrap` (re-sets passwords),
  then run `aws apprunner start-deployment --service-arn <apprunner_service_arn>`
  immediately after `-Task bootstrap`: until the service restarts, its
  connections with the old password fail.

## Operational notes

- Any change to `infra/prod/nat-user-data.sh` replaces the NAT instance: a few
  minutes with no outbound traffic for App Runner and tasks. Apply outside
  business hours.
- The ECR lifecycle keeps the 30 most recent images plus whatever `:prod` and `:candidate`
  currently point at (one never-expire rule per tag, priorities 1 and 2); older rollback
  targets beyond those are gone.
- If the migrate step in CI times out (~10 min), the Fargate task may still be
  running: check it with `aws ecs list-tasks --cluster esl-prod --family esl-prod-migrate`
  before re-running.
- Losing `infra/bootstrap`'s local state file is recoverable: `terraform import`
  the four bucket resources (the bucket has `prevent_destroy`).
- The GitHub OIDC trust matches the repo's `sub` claim exactly. This repo
  uses GitHub's immutable subjects (`repo:levstra@339920851/SquareRetailTags@1336255242:…`),
  set in `github_oidc_subject_prefix`. If `deploy` fails with "Not authorized
  to perform sts:AssumeRoleWithWebIdentity", compare that variable with
  `sub_claim_prefix` from
  `gh api repos/levstra/SquareRetailTags/actions/oidc/customization/sub`.

## Costs (~$40/mo)

us-west-2, approximate, monthly.

| Item | Cost |
|---|---|
| App Runner, 0.25 vCPU / 0.5 GB, always active (gateways long-poll) | ~$14.2 |
| RDS db.t4g.micro single-AZ + 20 GB gp3 (backups within free allowance) | ~$14.0 |
| NAT instance t4g.nano | ~$3.1 |
| Elastic IP (public IPv4 charge) | ~$3.7 |
| Secrets Manager, 7 secrets + RDS-managed master secret | ~$3.2 |
| Route 53 hosted zone | $0.5 |
| CloudWatch logs/alarms, ECR storage, S3 state, SNS, Fargate one-offs | ~$1-2 |
| Cognito (free tier), S3 gateway endpoint (free) | $0 |
| CloudFront + S3 for `levstra.com`, ACM certificate (free tier / free) | $0 |
| SES, $0.10 per 1,000 emails; email Lambda (free tier) | ~$0 |
| **Total** | **~$40** |
