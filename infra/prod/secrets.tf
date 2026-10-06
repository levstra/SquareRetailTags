resource "random_password" "db_admin" {
  length  = 40
  special = false
}

resource "random_password" "db_app" {
  length  = 40
  special = false
}

locals {
  db_endpoint = "${aws_db_instance.main.address}:${aws_db_instance.main.port}"
}

resource "aws_secretsmanager_secret" "db_admin_password" {
  name                    = "${local.name}/db/esl-admin-password"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "db_admin_password" {
  secret_id     = aws_secretsmanager_secret.db_admin_password.id
  secret_string = random_password.db_admin.result
}

resource "aws_secretsmanager_secret" "db_app_password" {
  name                    = "${local.name}/db/esl-app-password"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "db_app_password" {
  secret_id     = aws_secretsmanager_secret.db_app_password.id
  secret_string = random_password.db_app.result
}

# DATABASE_URL for the web app: the esl_app login (RLS enforced).
resource "aws_secretsmanager_secret" "database_url" {
  name                    = "${local.name}/db/app-url"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id     = aws_secretsmanager_secret.database_url.id
  secret_string = "postgresql+asyncpg://esl_app:${random_password.db_app.result}@${local.db_endpoint}/esl"
}

# ADMIN_DATABASE_URL: only ever injected into Fargate tasks.
resource "aws_secretsmanager_secret" "admin_database_url" {
  name                    = "${local.name}/db/admin-url"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "admin_database_url" {
  secret_id     = aws_secretsmanager_secret.admin_database_url.id
  secret_string = "postgresql+asyncpg://esl_admin:${random_password.db_admin.result}@${local.db_endpoint}/esl"
}

# Values are set by a human with `aws secretsmanager put-secret-value` (see
# docs/deploy.md) so they never enter Terraform state.
resource "aws_secretsmanager_secret" "square_application_secret" {
  name                    = "${local.name}/square/application-secret"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret" "square_webhook_signature_key" {
  name                    = "${local.name}/square/webhook-signature-key"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret" "token_encryption_key" {
  name                    = "${local.name}/app/token-encryption-key"
  recovery_window_in_days = 7
}
