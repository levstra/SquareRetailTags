output "region" {
  value = var.region
}

output "app_url" {
  value = local.app_url
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "task_security_group_id" {
  value = aws_security_group.tasks.id
}

output "task_definitions" {
  value = {
    admin     = aws_ecs_task_definition.admin.family
    migrate   = aws_ecs_task_definition.migrate.family
    bootstrap = aws_ecs_task_definition.bootstrap.family
  }
}

output "tasks_log_group" {
  value = aws_cloudwatch_log_group.tasks.name
}

output "apprunner_service_arn" {
  value = try(aws_apprunner_service.app[0].arn, "")
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "cognito_app_client_id" {
  value = aws_cognito_user_pool_client.web.id
}

output "cognito_authority" {
  value = local.cognito_authority
}

output "cognito_domain_url" {
  value = local.cognito_domain_url
}

output "github_deploy_role_arn" {
  value = aws_iam_role.github_deploy.arn
}

output "nat_public_ip" {
  value = aws_eip.nat.public_ip
}

output "secret_arns" {
  description = "Secrets whose values a human sets with put-secret-value."
  value = {
    square_application_secret    = aws_secretsmanager_secret.square_application_secret.arn
    square_webhook_signature_key = aws_secretsmanager_secret.square_webhook_signature_key.arn
    token_encryption_key         = aws_secretsmanager_secret.token_encryption_key.arn
  }
}
