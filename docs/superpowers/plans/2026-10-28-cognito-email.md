# Branded Cognito Email Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cognito's emails come from `no-reply@levstra.com` through SES, each with its own Levstra-styled wording, and pass SPF/DKIM/DMARC.

**Architecture:** A stdlib-only Python Lambda (`esl.accounts.cognito_emails`) is the pool's custom message trigger and renders one shared HTML layout per trigger. Terraform adds the SES domain identity, DNS (DKIM, MAIL FROM, DMARC), the Lambda, and the wordmark image on the existing `levstra.com` CloudFront site. The pool switches to SES only when `cognito_email_via_ses = true` (stage 3, after SES production access).

**Tech Stack:** Python 3.12 (Lambda, pytest), Terraform (`hashicorp/aws ~> 6.0`, `hashicorp/archive`), SES v2, Route 53, CloudFront, PowerShell 5.1 scripts, Node for the one-off wordmark render.

**Spec:** `docs/superpowers/specs/2026-10-28-cognito-email-design.md`

## Global Constraints

- From `Levstra <no-reply@levstra.com>`; Reply-To `levstra@levstra.com`.
- SES region us-west-2; Easy DKIM `RSA_2048_BIT`; MAIL FROM `mail.levstra.com`.
- DMARC record exactly `v=DMARC1; p=<dmarc_policy>; rua=mailto:levstra@levstra.com; adkim=r; aspf=r`; `dmarc_policy` default `"none"`.
- `cognito_email_via_ses` default `false`; with `false` the plan must not change `aws_cognito_user_pool.main`.
- Never touch the apex TXT record or the ForwardEmail MX records.
- `src/esl/accounts/cognito_emails.py` imports only the standard library; it is the whole Lambda package.
- Colours: accent `#6d3fd4`, text `#1c1726`, muted `#574f68`, footer `#9a93ad`, rule `#e7e1f0`. Fonts: `"Plus Jakarta Sans", Arial, Helvetica, sans-serif`; codes `"IBM Plex Mono", Consolas, "Courier New", monospace`.
- Wordmark URL `https://levstra.com/email/wordmark.png`, alt `Levstra`, displayed 28px high.
- Each `emailMessage` under 20,000 characters; never log codes or addresses.
- Terraform `for_each`/`count` keys must be known at plan time (the edge-certificate bug from PR #11): DKIM records use `count = 3`, not `for_each` over tokens.
- PowerShell examples quote `-var-file`: `"-var-file=prod.tfvars"`.

## Review Focus

1. A resent invite (`AdminCreateUser` with `MessageAction=RESEND`) arrives as `CustomMessage_AdminCreateUser`; it must get the invite email with `{username}` and `{####}` (test in Task 1).
2. An event with no `usernameParameter` (every trigger except invites) must not crash the handler (test in Task 1).
3. `/email/wordmark.png` must return 200 `image/png`, not the 302 redirect every other `levstra.com` path gets (curl check in Task 3).
4. Turning `cognito_email_via_ses` on must update the pool in place — the pool has `deletion_protection = "ACTIVE"`, and a replacement would fail (or lose every user) (plan check in Task 4).
5. Turning it back off must actually clear the custom message trigger on the pool, not just remove it from config (CLI check in Task 4).

---

### Task 1: Email renderer and Lambda handler

**Files:**
- Create: `src/esl/accounts/cognito_emails.py`
- Test: `tests/test_cognito_emails.py`

**Interfaces:**
- Produces: `handler(event: dict, context: object) -> dict` (Lambda entry `cognito_emails.handler`); `render(trigger: str, code: str, username: str | None, app_url: str) -> tuple[str, str] | None` returning `(subject, html)` or `None` for unmapped triggers; module constant `WORDMARK_URL = "https://levstra.com/email/wordmark.png"`; `APP_URL` read from env `APP_URL`, default `https://app.levstra.com`; `python -m esl.accounts.cognito_emails preview <dir>` writes `<trigger>.html` per mapped trigger.

- [ ] **Step 1: Write the failing tests**

```python
import ast
from pathlib import Path
import pytest
from esl.accounts import cognito_emails as ce

MAPPED = {
    "CustomMessage_AdminCreateUser": "You're invited to Levstra",
    "CustomMessage_ResendCode": "Your Levstra code",
    "CustomMessage_ForgotPassword": "Reset your Levstra password",
    "CustomMessage_UpdateUserAttribute": "Confirm your email for Levstra",
    "CustomMessage_VerifyUserAttribute": "Confirm your email for Levstra",
    "CustomMessage_Authentication": "Your Levstra sign-in code",
}

def event(trigger, username="{username}"):
    request = {"codeParameter": "{####}", "userAttributes": {"email": "a@b.co"}}
    if username is not None:
        request["usernameParameter"] = username
    return {"triggerSource": trigger, "request": request, "response": {}}

@pytest.mark.parametrize("trigger, subject", MAPPED.items())
def test_each_trigger_gets_subject_and_code(trigger, subject):
    out = ce.handler(event(trigger), None)["response"]
    assert out["emailSubject"] == subject
    assert "{####}" in out["emailMessage"]
    assert len(out["emailMessage"]) < 20_000

def test_invite_contains_username_and_sign_in_link():
    html = ce.handler(event("CustomMessage_AdminCreateUser"), None)["response"]["emailMessage"]
    assert "{username}" in html and "https://app.levstra.com" in html and "7 days" in html

def test_forgot_password_states_one_hour():
    html = ce.handler(event("CustomMessage_ForgotPassword"), None)["response"]["emailMessage"]
    assert "1 hour" in html

@pytest.mark.parametrize("trigger", [t for t in MAPPED if t != "CustomMessage_AdminCreateUser"])
def test_missing_username_parameter_is_fine(trigger):
    assert ce.handler(event(trigger, username=None), None)["response"]["emailSubject"]

def test_wordmark_image():
    html = ce.render("CustomMessage_ForgotPassword", "{####}", None, "https://app.levstra.com")[1]
    assert f'src="{ce.WORDMARK_URL}"' in html and 'alt="Levstra"' in html
    assert 'height="28"' in html and "width=" in html

def test_unknown_trigger_passes_through():
    e = event("CustomMessage_SignUp")
    assert ce.handler(e, None) == event("CustomMessage_SignUp")

def test_app_url_from_environment(monkeypatch):
    monkeypatch.setattr(ce, "APP_URL", "https://example.test")
    html = ce.handler(event("CustomMessage_AdminCreateUser"), None)["response"]["emailMessage"]
    assert "https://example.test" in html

def test_stdlib_only():
    tree = ast.parse(Path(ce.__file__).read_text())
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert "esl" not in names and names <= {"html", "json", "logging", "os", "sys", "pathlib"}

def test_preview_writes_one_file_per_trigger(tmp_path):
    ce.main(["preview", str(tmp_path)])
    assert {p.stem for p in tmp_path.glob("*.html")} == set(MAPPED)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_cognito_emails.py -q`
Expected: collection error, `No module named 'esl.accounts.cognito_emails'`.

- [ ] **Step 3: Implement `cognito_emails.py`**

A dict from trigger to `(subject, heading, paragraphs, code_label)` with the exact copy in spec §3, one `_layout(heading, paragraphs, code_html, footer)` producing style B (spec D6; layout and colours in spec §4.1) as table-based HTML with inline styles only, `<link>` to Google Fonts for Plus Jakarta Sans and IBM Plex Mono in `<head>`, wordmark `<img>` per Global Constraints (width = 2× PNG width ÷ 2, set once Task 2 has fixed the PNG size; use `height="28"` now and the measured width), invite block shows `Email: {username}` and `Temporary password: {####}` and a "Sign in" link to `app_url`. Escape all copy with `html.escape` (placeholders are inserted verbatim). `handler` logs `trigger_source` only, at INFO, via `logging`. `main(argv)` handles `preview <dir>`; `if __name__ == "__main__": main(sys.argv[1:])`.

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_cognito_emails.py -q`
Expected: all pass.

- [ ] **Step 5: Preview by eye**

Run: `.venv\Scripts\python -m esl.accounts.cognito_emails preview "$env:TEMP\esl-emails"` and open each file in a browser (image will 404 until Task 3 deploys; alt text shows).
Expected: matches style B from the brainstorm screen.

- [ ] **Step 6: Commit**

```bash
git add src/esl/accounts/cognito_emails.py tests/test_cognito_emails.py
git commit -m "Cognito custom message emails in the Levstra style"
```

---

### Task 2: Wordmark image

**Files:**
- Create: `scripts/render_email_wordmark.mjs`
- Create: `infra/prod/branding/email-wordmark.png`
- Modify: `src/esl/accounts/cognito_emails.py` (wordmark `width` attribute)
- Test: `tests/test_cognito_emails.py`

**Interfaces:**
- Produces: `infra/prod/branding/email-wordmark.png`, 56px high, transparent background, "Levstra" in Plus Jakarta Sans Bold `#6d3fd4`, ~2px padding.

- [ ] **Step 1: Write the failing test**

```python
def test_wordmark_png_matches_img_tag():
    import struct
    png = (Path(__file__).parents[1] / "infra/prod/branding/email-wordmark.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png[16:24])
    assert height == 56
    html = ce.render("CustomMessage_ForgotPassword", "{####}", None, "https://app.levstra.com")[1]
    assert f'width="{width // 2}"' in html
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_cognito_emails.py::test_wordmark_png_matches_img_tag -q`; Expected: FileNotFoundError.

- [ ] **Step 3: Write `scripts/render_email_wordmark.mjs`**

`node scripts/render_email_wordmark.mjs <out.png>`: in a fresh `os.tmpdir()` folder, `npm install @resvg/resvg-js@2.6.2`, download `PlusJakartaSans-Bold.ttf` from `https://github.com/google/fonts/raw/main/ofl/plusjakartasans/PlusJakartaSans%5Bwght%5D.ttf` (variable font; set `font-weight="700"`), render an SVG `<text>` "Levstra" with `fontFiles` and `loadSystemFonts: false`, `fitTo: { mode: "height", value: 56 }`, trimmed width. Header comment: when to rerun and that it adds no project dependency.

- [ ] **Step 4: Render, look at it, set the width**

Run: `node scripts/render_email_wordmark.mjs infra/prod/branding/email-wordmark.png`, open the PNG and check it is the website's "Levstra" in purple. Put `width // 2` into the `<img>` in `cognito_emails.py`.

- [ ] **Step 5: Run tests** — `pytest tests/test_cognito_emails.py -q`; Expected: all pass.

- [ ] **Step 6: Commit** — `git add scripts/render_email_wordmark.mjs infra/prod/branding/email-wordmark.png src/esl/accounts/cognito_emails.py tests/test_cognito_emails.py` and commit "Email wordmark in Plus Jakarta Sans".

---

### Task 3: SES, DNS, Lambda and wordmark hosting (stage 1 infrastructure)

**Files:**
- Create: `infra/prod/email.tf`
- Modify: `infra/prod/variables.tf`, `infra/prod/versions.tf`, `infra/prod/site.tf`

**Interfaces:**
- Consumes: `cognito_emails.handler` (Task 1), `email-wordmark.png` (Task 2), `aws_sns_topic.alarms`, `aws_s3_bucket.site[0]`, `data.aws_route53_zone.main[0]`, `local.name`, `local.app_url`.
- Produces: `aws_sesv2_email_identity.main[0]` (its `arn` for Task 4), `aws_lambda_function.cognito_emails[0]` (its `arn` for Task 4), variables `cognito_email_via_ses` (bool, default `false`) and `dmarc_policy` (string, default `"none"`, validation `contains(["none","quarantine","reject"], …)`).

- [ ] **Step 1: Add the variables and the archive provider**

`variables.tf`: the two variables above, with descriptions pointing at `docs/deploy.md` "Email". `versions.tf`: `archive = { source = "hashicorp/archive", version = "~> 2.4" }`.

- [ ] **Step 2: Write `email.tf`** — every resource `count = var.deploy_service ? 1 : 0` (or `count = var.deploy_service ? 3 : 0` for DKIM):

- `aws_sesv2_email_identity.main`: `email_identity = var.hosted_zone_name`, `dkim_signing_attributes { next_signing_key_length = "RSA_2048_BIT" }`.
- `aws_route53_record.ses_dkim`: `count = 3`, name `"${aws_sesv2_email_identity.main[0].dkim_signing_attributes[0].tokens[count.index]}._domainkey.${var.hosted_zone_name}"`, CNAME to `"<token>.dkim.amazonses.com"`, ttl 1800.
- `aws_sesv2_email_identity_mail_from_attributes.main`: `mail_from_domain = "mail.${var.hosted_zone_name}"`, `behavior_on_mx_failure = "USE_DEFAULT_VALUE"`.
- `aws_route53_record` MX `mail.<zone>` → `10 feedback-smtp.${var.region}.amazonses.com`; TXT `mail.<zone>` → `v=spf1 include:amazonses.com -all`; TXT `_dmarc.<zone>` per Global Constraints.
- `data.archive_file.cognito_emails`: `source_file = "${path.module}/../../src/esl/accounts/cognito_emails.py"`, `output_path = "${path.module}/.build/cognito_emails.zip"` (add `infra/prod/.build/` to `infra/.gitignore`).
- `aws_iam_role` + `AWSLambdaBasicExecutionRole` attachment; `aws_cloudwatch_log_group` `/aws/lambda/${local.name}-cognito-emails`, 30 days.
- `aws_lambda_function.cognito_emails`: `function_name = "${local.name}-cognito-emails"`, `handler = "cognito_emails.handler"`, `runtime = "python3.12"`, `memory_size = 128`, `timeout = 5`, `source_code_hash` from the archive, `environment { variables = { APP_URL = local.app_url } }`, `depends_on` the log group.
- `aws_lambda_permission`: principal `cognito-idp.amazonaws.com`, `source_arn = aws_cognito_user_pool.main.arn`.
- `aws_cloudwatch_metric_alarm`: `AWS/Lambda` `Errors`, `FunctionName` dimension, Sum > 0 over 300s, 1 period, `treat_missing_data = "notBreaching"`, `alarm_actions = [aws_sns_topic.alarms.arn]`.
- `aws_s3_object.email_wordmark`: bucket `aws_s3_bucket.site[0].id`, key `email/wordmark.png`, `source`/`etag = filemd5(...)`, `content_type = "image/png"`, `cache_control = "public, max-age=31536000"`.

- [ ] **Step 3: Exempt `/email/*` from the redirect in `site.tf`**

On `aws_cloudfront_distribution.site`, add `ordered_cache_behavior` with `path_pattern = "/email/*"`, same `target_origin_id`, methods, cache policy, `viewer_protocol_policy` and `compress` as the default behavior, and no `function_association`. Update the file's header comment.

- [ ] **Step 4: Validate and plan (read-only)**

Run: `terraform -chdir=infra/prod init -backend-config=backend.hcl` then `terraform -chdir=infra/prod plan "-var-file=prod.tfvars"`
Expected: only creates for the resources above, plus an in-place update of `aws_cloudfront_distribution.site[0]`; **no change to `aws_cognito_user_pool.main`**, nothing replaced or destroyed.

- [ ] **Step 5: Commit** — `git add infra/ && git commit -m "SES sending domain, email DNS, Cognito email Lambda (not yet wired to the pool)"`.

- [ ] **Step 6 (after merge, stage 1, operator): apply and check**

Run `terraform -chdir=infra/prod apply "-var-file=prod.tfvars"`, then:
- `aws sesv2 get-email-identity --email-identity levstra.com --query '{dkim:DkimAttributes.Status,mailFrom:MailFromAttributes.MailFromDomainStatus}'` → both `SUCCESS` (allow up to an hour).
- `curl.exe -sI https://levstra.com/email/wordmark.png` → `200`, `content-type: image/png`.
- `curl.exe -sI https://levstra.com/` → still `302`.

---

### Task 4: Pool switch behind `cognito_email_via_ses`

**Files:**
- Modify: `infra/prod/cognito.tf` (`aws_cognito_user_pool.main`)

**Interfaces:**
- Consumes: `aws_sesv2_email_identity.main[0].arn`, `aws_lambda_function.cognito_emails[0].arn`, `var.cognito_email_via_ses` (Task 3).

- [ ] **Step 1: Add the email configuration and trigger**

`email_configuration` with `email_sending_account = var.cognito_email_via_ses ? "DEVELOPER" : "COGNITO_DEFAULT"` and `source_arn`, `from_email_address`, `reply_to_email_address` set only when enabled (else `null`); `lambda_config { custom_message = … }` only when enabled (a `dynamic` block). Comment: why it's gated (SES sandbox) and the rollback.

- [ ] **Step 2: Plan with the flag off**

Run: `terraform -chdir=infra/prod plan "-var-file=prod.tfvars"`
Expected: `aws_cognito_user_pool.main` unchanged. If it shows a diff, adjust the blocks until it doesn't; the false case must be a no-op.

- [ ] **Step 3: Plan with the flag on (do not apply)**

Run: `terraform -chdir=infra/prod plan "-var-file=prod.tfvars" -var cognito_email_via_ses=true`
Expected: `aws_cognito_user_pool.main` **updated in-place** (`email_configuration`, `lambda_config`), never "must be replaced".

- [ ] **Step 4: Commit** — `git commit -am "Send Cognito email through SES when cognito_email_via_ses is set"`.

- [ ] **Step 5 (stage 3, operator, only after SES production access): switch and verify, including rollback**

Set `cognito_email_via_ses = true` in `prod.tfvars`, plan (as Step 3), apply. Trigger "Forgot password" for your own account on `https://app.levstra.com`; the email arrives from `no-reply@levstra.com`, styled, with `mailed-by: mail.levstra.com` and `signed-by: levstra.com` in Gmail's "Show original" (SPF/DKIM/DMARC `PASS`).
Rollback drill (on the dev pool via Task 5's script, not prod): disable, then `aws cognito-idp describe-user-pool --user-pool-id <dev> --query 'UserPool.LambdaConfig'` → `{}`. For prod, if a rollback plan leaves `LambdaConfig.CustomMessage` set, clear it with `aws cognito-idp update-user-pool` and record that in `docs/deploy.md`.

---

### Task 5: Dev pool script

**Files:**
- Create: `scripts/dev_cognito_email.ps1`

**Interfaces:**
- Consumes: SES identity `levstra.com` (Task 3 stage 1), `src/esl/accounts/cognito_emails.py` (Task 1).
- Produces: `.\scripts\dev_cognito_email.ps1 -UserPoolId <id> -RecipientEmail <addr> [-Region us-west-2] [-Disable]`.

- [ ] **Step 1: Write the script** (same style as `dev_cognito_branding.ps1`: header comment, `Invoke-Aws`, `$ErrorActionPreference = 'Stop'` with the `Continue` workaround around expected-failure lookups):
  1. Refuse if the pool's name is `esl-prod`.
  2. Stop with "apply stage 1 first" if `aws sesv2 get-email-identity --email-identity levstra.com` fails. Never create it.
  3. `aws sesv2 create-email-identity --email-identity <RecipientEmail>` if missing; tell the user to click the verification link.
  4. Zip `cognito_emails.py` (`Compress-Archive`), create or update role `esl-dev-cognito-emails` (basic execution) and function `esl-dev-cognito-emails` (python3.12, `cognito_emails.handler`, `APP_URL=http://localhost:5173`); add invoke permission for the dev pool ARN.
  5. `update-user-pool` setting `EmailConfiguration` (DEVELOPER, SES identity ARN, From/Reply-To from Global Constraints) and `LambdaConfig.CustomMessage`. `update-user-pool` resets omitted settings, so read the current pool first and pass its `AutoVerifiedAttributes`, `AdminCreateUserConfig`, `AccountRecoverySetting`, `Policies`, `UserPoolAddOns`/tier through unchanged.
  6. `-Disable` sets `COGNITO_DEFAULT` and empty `LambdaConfig` the same way.

- [ ] **Step 2: Run against the dev pool**

Run: `.\scripts\dev_cognito_email.ps1 -UserPoolId us-west-2_smbwh9d1R -RecipientEmail barrettlevi28@gmail.com`, verify the address, then use "Forgot password" on `http://localhost:5173` and an `admin-create-user --message-action RESEND` for the dev user.
Expected: both emails arrive in Gmail, styled, from `no-reply@levstra.com`; the dev pool still signs in.

- [ ] **Step 3: `-Disable` drill** — run with `-Disable`; `describe-user-pool` shows `COGNITO_DEFAULT` and `LambdaConfig` `{}`; a forgot-password email arrives in Cognito's default form. Re-enable.

- [ ] **Step 4: Commit** — `git add scripts/dev_cognito_email.ps1 && git commit -m "Dev script: Cognito email through SES on the dev pool"`.

---

### Task 6: Docs and rollout checklist

**Files:**
- Modify: `docs/deploy.md` (new "Email [CHANGES AWS] [CHANGES DNS]" section after "Sign-in domain and the bare domain"; cost table row "SES — $0.10 per 1,000 emails")

- [ ] **Step 1: Write the section**, containing:
  - Stages 1–3 and the DMARC step from spec §6, with the commands from Tasks 3–5 (`-var-file` quoted).
  - **SES production access request text:** use case "transactional only: account invitations, password reset and verification codes for Levstra's invite-only dashboard (a few dozen users)", no marketing, how addresses are obtained (an owner or operator invites them), bounce/complaint handling (SES account-level suppression list; addresses come from deliberate invites).
  - **Deliverability check (mail-tester):** send a forgot-password email from the dev pool to the address mail-tester.com gives you; record the score here. **If the score is below 9/10, or the report flags the missing plain-text part (`MIME_HTML_ONLY` or similar), open a follow-up to add a plain-text version.** Cognito's custom message trigger is HTML-only, so that follow-up means a Cognito *custom email sender* Lambda (spec D11 / approach C) and needs its own spec.
  - Invoicing checklist (D10): before any new sender uses `@levstra.com`, add its SPF include / DKIM records, confirm DMARC passes with mail-tester, then send.
  - Rollback: `cognito_email_via_ses = false`, apply, check `LambdaConfig` is empty (Task 4 Step 5).

- [ ] **Step 2: Full test suite** — `docker compose up -d` then `.venv/Scripts/python -m pytest -q`; Expected: all pass.

- [ ] **Step 3: Commit** — `git commit -am "Deploy docs: branded Cognito email rollout"`.

---

### Rollout checkpoints (operator, after merge — not code)

- [ ] Stage 1 apply + checks (Task 3 Step 6).
- [ ] Request SES production access (text in `docs/deploy.md`).
- [ ] Dev pool test (Task 5 Step 2).
- [ ] **Mail-tester check — reminder requested by the user.** Run it, record the score in `docs/deploy.md`, and **decide on the plain-text version from the score** (rule in Task 6). Do not skip this before stage 3.
- [ ] Stage 3 switch after SES approval (Task 4 Step 5).
- [ ] About two weeks after stage 3: review DMARC reports at `levstra@levstra.com`; if only aligned SES mail, set `dmarc_policy = "quarantine"` and apply.
