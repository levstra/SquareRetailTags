# Branded Cognito Email — Design

**Date:** 2026-10-28
**Status:** Draft, awaiting review
**Repos:** SquareRetailTags only

## 1. Goal

Every email Cognito sends to Levstra users comes from `no-reply@levstra.com`,
says plainly what it is for, uses the Levstra look (the dashboard's colours and
font), and lands in the inbox rather than spam.

Today the pool sends through Cognito's shared sender
(`EmailSendingAccount = COGNITO_DEFAULT`, `no-reply@verificationemail.com`),
with Cognito's stock wording and a ~50/day cap. `levstra.com` has no SPF or
DMARC record, and SES has no identities (account in the SES sandbox).

## 2. Decisions

| # | Decision |
|---|---|
| D1 | From `Levstra <no-reply@levstra.com>`, Reply-To `levstra@levstra.com` (already forwards to the owner's Gmail through ForwardEmail). |
| D2 | Send through SES (us-west-2, same region as the pool) with Easy DKIM (RSA 2048) on `levstra.com` and a custom MAIL FROM of `mail.levstra.com`. |
| D3 | DMARC starts at `p=none` with aggregate reports to `levstra@levstra.com`, and moves to `p=quarantine` after about two weeks of clean reports. |
| D4 | The apex SPF/TXT record and the ForwardEmail MX records are not touched. They were created by hand outside Terraform, and SES alignment comes from DKIM and the MAIL FROM subdomain. |
| D5 | Content comes from a Cognito **custom message** Lambda trigger (approach B), not from the pool's three static templates. Cognito still sends the mail. |
| D6 | Visual style B, "minimal letter": white background, purple wordmark, dark heading, grey body, code in purple monospace, thin rule above a grey footer. |
| D7 | The "Levstra" wordmark is a PNG drawn in Plus Jakarta Sans Bold, `#6d3fd4`, 2× resolution, alt text "Levstra", served from `https://levstra.com/email/wordmark.png`. Everything else is real text. |
| D8 | Body font stack `"Plus Jakarta Sans", Arial, Helvetica, sans-serif`, loaded from Google Fonts with a `<link>`; codes `"IBM Plex Mono", Consolas, "Courier New", monospace`. Clients that strip web fonts get the fallbacks (accepted). |
| D9 | Rollout in three stages (§6). The switch to SES is behind `cognito_email_via_ses`, default `false`, because the SES sandbox can't deliver to unverified customer addresses. |
| D10 | Future invoicing mail from `levstra.com` must get SPF/DKIM before it sends anything; `docs/deploy.md` carries the checklist. |
| D11 | Out of scope: invoicing email, business name in invites, enabling MFA, a plain-text alternative part (Cognito sends HTML only; that would need a custom email sender). |

## 3. Emails

Cognito passes a `triggerSource`; the Lambda returns `emailSubject` and
`emailMessage` and leaves `smsMessage` alone.

| `triggerSource` | Subject | Heading | Body | Code shown |
|---|---|---|---|---|
| `CustomMessage_AdminCreateUser` | You're invited to Levstra | You're invited to Levstra | Sign in with the email address and temporary password below; you'll choose your own password. Expires in 7 days. **Sign in** link to `https://app.levstra.com`. | Email `{username}` and temporary password `{####}` |
| `CustomMessage_ResendCode` | Your Levstra code | Here's your new code | Use it to confirm your email address. | `{####}` |
| `CustomMessage_ForgotPassword` | Reset your Levstra password | Reset your password | Use this code to choose a new password. It expires in 1 hour. "Didn't ask for this? You can ignore this email; your password won't change." | `{####}` |
| `CustomMessage_UpdateUserAttribute`, `CustomMessage_VerifyUserAttribute` | Confirm your email for Levstra | Confirm your email address | Enter this code in Levstra to confirm this address. | `{####}` |
| `CustomMessage_Authentication` | Your Levstra sign-in code | Your sign-in code | Enter this code to finish signing in. "Didn't try to sign in? Change your password." | `{####}` |
| anything else (e.g. `CustomMessage_SignUp`, unused: pool is invite-only) | unchanged | — | — | — |

`{username}` and `{####}` are emitted literally from
`event.request.usernameParameter` and `codeParameter`; Cognito substitutes
them. No user attribute values are put into the HTML. Lifetimes are stated only
where they are fixed: invite 7 days (`temporary_password_validity_days`) and
forgot-password code 1 hour. Verification codes (24 hours) and sign-in codes
(configurable) carry no expiry text.

The Sign-in link uses `APP_URL` from the Lambda's environment (Terraform:
`local.app_url`). A resent invite (`AdminCreateUser` with
`MessageAction=RESEND`) arrives as `CustomMessage_AdminCreateUser` and gets the
invite email.

## 4. Components

### 4.1 Lambda — `src/esl/accounts/cognito_emails.py`

- Standard library only; no imports from the rest of `esl`, so the file is the
  whole deployment package.
- `handler(event, context)` looks up the trigger in a table of
  `(subject, heading, paragraphs, code_block)` entries, renders them into one
  shared layout function, sets `event["response"]`, returns `event`.
  Unknown triggers return `event` unchanged.
- Layout: table-based HTML with inline styles, max width 520px, style B
  colours from `frontend/src/index.css` (accent `#6d3fd4`, text `#1c1726`,
  muted `#574f68`, footer `#9a93ad`, rule `#e7e1f0`). Footer:
  "Levstra · Electronic shelf labels" and "You're receiving this because an
  account for this address exists at levstra.com."
- Logs the trigger source only (never the code or address) at INFO.
- Python 3.12, 128 MB, 5 s timeout.

### 4.2 Wordmark — `infra/prod/branding/email-wordmark.png`

- Rendered by `scripts/render_email_wordmark.mjs` from "Levstra" in Plus
  Jakarta Sans Bold, height 56px for 28px display. The script installs
  `@resvg/resvg-js@2.6.2` and the OFL font file into a temp directory, so the
  project gains no dependency; it only runs when the wordmark changes.
- Committed; Terraform uploads it as `email/wordmark.png` to the site bucket
  with `Content-Type: image/png` and a one-year cache.

### 4.3 Terraform — `infra/prod/email.tf` (new)

All gated on `deploy_service` like `site.tf`.

- `aws_sesv2_email_identity` for `levstra.com` (Easy DKIM, RSA_2048_BIT) and
  three DKIM CNAME `aws_route53_record`s.
- `aws_sesv2_email_identity_mail_from_attributes` with `mail.levstra.com`,
  `behavior_on_mx_failure = USE_DEFAULT_VALUE`; MX record
  `10 feedback-smtp.us-west-2.amazonses.com` and TXT `v=spf1 include:amazonses.com -all`
  on `mail.levstra.com`.
- TXT `_dmarc.levstra.com`: `v=DMARC1; p=${var.dmarc_policy}; rua=mailto:levstra@levstra.com; adkim=r; aspf=r`.
  New variable `dmarc_policy`, default `"none"`.
- `archive_file` zipping `src/esl/accounts/cognito_emails.py`;
  `aws_lambda_function` `esl-prod-cognito-emails` with a basic execution role,
  a 30-day `aws_cloudwatch_log_group`, `aws_lambda_permission` for
  `cognito-idp.amazonaws.com` scoped to the pool ARN, and a CloudWatch alarm on
  `Errors > 0` over 5 minutes to the existing alarm SNS topic.
- `aws_s3_object` for the wordmark.

### 4.4 Terraform — changes to existing files

- `variables.tf`: `cognito_email_via_ses` (bool, default `false`),
  `dmarc_policy` (string, default `"none"`, validated to none/quarantine/reject).
- `cognito.tf`, on `aws_cognito_user_pool.main`, when
  `cognito_email_via_ses`:
  - `email_configuration { email_sending_account = "DEVELOPER", source_arn = <identity ARN>, from_email_address = "Levstra <no-reply@levstra.com>", reply_to_email_address = "levstra@levstra.com" }`
  - `lambda_config { custom_message = <lambda ARN> }`
  Otherwise `COGNITO_DEFAULT` and no trigger (today's state). Uses `dynamic`
  blocks so the `false` plan is a no-op on the pool.
- `site.tf`: an `ordered_cache_behavior` for `/email/*` on the site
  distribution, same origin and cache policy, **without** the redirect
  function.
- `versions.tf`: add the `hashicorp/archive` provider.

### 4.5 Dev

- `scripts/dev_cognito_email.ps1 -UserPoolId <id> -RecipientEmail <you>`:
  requires the `levstra.com` SES identity from stage 1 (the account has one
  identity per domain, owned by Terraform; the script stops with a message if
  it is missing and never creates it), verifies the recipient address in the
  SES sandbox, deploys the same Lambda as `esl-dev-cognito-emails`, and sets the
  dev pool's email configuration and custom message trigger. Never takes the
  production pool id (refuses `esl-prod` by name).
- `python -m esl.accounts.cognito_emails preview <dir>` writes one HTML file
  per trigger for browser preview.

### 4.6 Docs — `docs/deploy.md`, new section "Email"

Stage-by-stage commands (§6), the SES production access request text, the
DMARC change, the invoicing checklist, rollback, and the mail-tester check.

## 5. Error handling

- If the Lambda errors or times out, Cognito does not send that email and the
  calling action fails (invite, forgot password). Mitigations: no network calls
  or external imports in the handler, a test per trigger, and the Errors alarm.
- Unknown `triggerSource`: pass through unchanged so Cognito sends its default
  message.
- SES bounces and complaints: the SES account-level suppression list (on by
  default) stops repeat sends to bad addresses. No custom handling.
- Rollback: set `cognito_email_via_ses = false` and apply. The pool returns to
  `COGNITO_DEFAULT` with no trigger; SES and DNS records stay and do nothing.

## 6. Rollout

1. **Stage 1** — merge, `terraform apply` with defaults. Creates SES identity,
   DNS, Lambda and wordmark; the pool is unchanged. Check SES shows the domain
   and MAIL FROM as verified.
2. **Stage 2** — request SES production access (console: SES → Account
   dashboard → Request production access; text in `docs/deploy.md`). Meanwhile
   run `scripts/dev_cognito_email.ps1` against the dev pool, test there, and send one email to mail-tester.com; target 9/10 or
   better with SPF, DKIM and DMARC passing.
3. **Stage 3** — after approval, set `cognito_email_via_ses = true` in
   `prod.tfvars`, plan (only the pool's email configuration and Lambda config
   change in place), apply, send yourself a forgot-password email.
4. **About two weeks later** — if DMARC reports show only aligned SES mail, set
   `dmarc_policy = "quarantine"` and apply.

## 7. Testing

`tests/test_cognito_emails.py`:

- Each mapped trigger returns a subject and an HTML message containing the
  literal `{####}`; `AdminCreateUser` also contains `{username}`.
- Every message is under 20,000 characters.
- The wordmark `<img>` has `alt="Levstra"`, explicit width and height, and an
  `https://levstra.com/email/` URL.
- An unknown trigger returns the event unchanged.
- The module imports nothing from `esl` (the Lambda zip contains only this
  file).

Manual: dev-pool send to Gmail and to an Apple Mail client, light and dark
mode; mail-tester score; `terraform plan` at stage 3 shows only in-place pool
changes.
