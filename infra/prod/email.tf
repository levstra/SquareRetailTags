# Cognito email from no-reply@<zone> through SES (docs/deploy.md "Email").
#
# Everything here is inert until cognito_email_via_ses points the pool at it
# (cognito.tf). The apex TXT and the ForwardEmail MX records were made by hand
# and are not managed here; alignment comes from DKIM and the MAIL FROM
# subdomain, so they don't need to change.

resource "aws_sesv2_email_identity" "main" {
  count          = var.deploy_service ? 1 : 0
  email_identity = var.hosted_zone_name
  dkim_signing_attributes {
    next_signing_key_length = "RSA_2048_BIT"
  }
}

# Easy DKIM always has three tokens; count, not for_each, because the tokens
# aren't known until the identity exists.
resource "aws_route53_record" "ses_dkim" {
  count   = var.deploy_service ? 3 : 0
  zone_id = data.aws_route53_zone.main[0].zone_id
  name    = "${aws_sesv2_email_identity.main[0].dkim_signing_attributes[0].tokens[count.index]}._domainkey.${var.hosted_zone_name}"
  type    = "CNAME"
  ttl     = 1800
  records = ["${aws_sesv2_email_identity.main[0].dkim_signing_attributes[0].tokens[count.index]}.dkim.amazonses.com"]
}

resource "aws_sesv2_email_identity_mail_from_attributes" "main" {
  count                  = var.deploy_service ? 1 : 0
  email_identity         = aws_sesv2_email_identity.main[0].email_identity
  mail_from_domain       = "mail.${var.hosted_zone_name}"
  behavior_on_mx_failure = "USE_DEFAULT_VALUE"
}

resource "aws_route53_record" "mail_from_mx" {
  count   = var.deploy_service ? 1 : 0
  zone_id = data.aws_route53_zone.main[0].zone_id
  name    = "mail.${var.hosted_zone_name}"
  type    = "MX"
  ttl     = 1800
  records = ["10 feedback-smtp.${var.region}.amazonses.com"]
}

resource "aws_route53_record" "mail_from_spf" {
  count   = var.deploy_service ? 1 : 0
  zone_id = data.aws_route53_zone.main[0].zone_id
  name    = "mail.${var.hosted_zone_name}"
  type    = "TXT"
  ttl     = 1800
  records = ["v=spf1 include:amazonses.com -all"]
}

resource "aws_route53_record" "dmarc" {
  count   = var.deploy_service ? 1 : 0
  zone_id = data.aws_route53_zone.main[0].zone_id
  name    = "_dmarc.${var.hosted_zone_name}"
  type    = "TXT"
  ttl     = 1800
  records = ["v=DMARC1; p=${var.dmarc_policy}; rua=mailto:levstra@levstra.com; adkim=r; aspf=r"]
}

# The custom message trigger: one stdlib-only file is the whole package.
data "archive_file" "cognito_emails" {
  type        = "zip"
  source_file = "${path.module}/../../src/esl/accounts/cognito_emails.py"
  output_path = "${path.module}/.build/cognito_emails.zip"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cognito_emails" {
  count              = var.deploy_service ? 1 : 0
  name               = "${local.name}-cognito-emails"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "cognito_emails_logs" {
  count      = var.deploy_service ? 1 : 0
  role       = aws_iam_role.cognito_emails[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_cloudwatch_log_group" "cognito_emails" {
  count             = var.deploy_service ? 1 : 0
  name              = "/aws/lambda/${local.name}-cognito-emails"
  retention_in_days = 30
}

resource "aws_lambda_function" "cognito_emails" {
  count            = var.deploy_service ? 1 : 0
  function_name    = "${local.name}-cognito-emails"
  role             = aws_iam_role.cognito_emails[0].arn
  handler          = "cognito_emails.handler"
  runtime          = "python3.12"
  memory_size      = 128
  timeout          = 5
  filename         = data.archive_file.cognito_emails.output_path
  source_code_hash = data.archive_file.cognito_emails.output_base64sha256
  environment {
    variables = { APP_URL = local.app_url }
  }
  depends_on = [aws_cloudwatch_log_group.cognito_emails, aws_iam_role_policy_attachment.cognito_emails_logs]
}

resource "aws_lambda_permission" "cognito_emails" {
  count         = var.deploy_service ? 1 : 0
  statement_id  = "AllowCognitoInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cognito_emails[0].function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.main.arn
}

# A failing trigger means Cognito sends nothing and the invite or reset fails.
resource "aws_cloudwatch_metric_alarm" "cognito_emails_errors" {
  count               = var.deploy_service ? 1 : 0
  alarm_name          = "${local.name}-cognito-emails-errors"
  alarm_description   = "The Cognito email Lambda failed; that email was not sent."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.cognito_emails[0].function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

# Served from https://<zone>/email/wordmark.png (site.tf exempts /email/*
# from the redirect).
resource "aws_s3_object" "email_wordmark" {
  count         = var.deploy_service ? 1 : 0
  bucket        = aws_s3_bucket.site[0].id
  key           = "email/wordmark.png"
  source        = "${path.module}/branding/email-wordmark.png"
  etag          = filemd5("${path.module}/branding/email-wordmark.png")
  content_type  = "image/png"
  cache_control = "public, max-age=31536000"
}
