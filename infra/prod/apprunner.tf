data "aws_iam_policy_document" "apprunner_build_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["build.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apprunner_access" {
  name               = "${local.name}-apprunner-ecr"
  assume_role_policy = data.aws_iam_policy_document.apprunner_build_assume.json
}

resource "aws_iam_role_policy_attachment" "apprunner_access" {
  role       = aws_iam_role.apprunner_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

data "aws_iam_policy_document" "apprunner_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["tasks.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apprunner_instance" {
  name               = "${local.name}-apprunner-instance"
  assume_role_policy = data.aws_iam_policy_document.apprunner_tasks_assume.json
}

locals {
  # No ADMIN_DATABASE_URL here: the web app never gets esl_admin.
  runtime_secret_arns = {
    DATABASE_URL                 = aws_secretsmanager_secret.database_url.arn
    SQUARE_APPLICATION_SECRET    = aws_secretsmanager_secret.square_application_secret.arn
    SQUARE_WEBHOOK_SIGNATURE_KEY = aws_secretsmanager_secret.square_webhook_signature_key.arn
    TOKEN_ENCRYPTION_KEY         = aws_secretsmanager_secret.token_encryption_key.arn
  }
}

resource "aws_iam_role_policy" "apprunner_instance" {
  name = "runtime"
  role = aws_iam_role.apprunner_instance.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = values(local.runtime_secret_arns)
      },
      {
        # Owners invite/disable staff from the dashboard (step 2).
        Effect   = "Allow"
        Action   = ["cognito-idp:AdminCreateUser", "cognito-idp:AdminDeleteUser", "cognito-idp:AdminDisableUser", "cognito-idp:AdminEnableUser"]
        Resource = aws_cognito_user_pool.main.arn
      },
    ]
  })
}

resource "aws_apprunner_vpc_connector" "main" {
  vpc_connector_name = local.name
  subnets            = aws_subnet.private[*].id
  security_groups    = [aws_security_group.apprunner.id]
}

resource "aws_apprunner_auto_scaling_configuration_version" "main" {
  auto_scaling_configuration_name = local.name
  min_size                        = 1
  max_size                        = 2
  max_concurrency                 = 100
}

resource "aws_apprunner_service" "app" {
  count        = var.deploy_service ? 1 : 0
  service_name = local.name

  lifecycle {
    precondition {
      condition     = var.square_application_id != ""
      error_message = "Set square_application_id in prod.tfvars before deploy_service = true."
    }
  }

  source_configuration {
    auto_deployments_enabled = false # CI runs migrations first, then start-deployment
    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_access.arn
    }
    image_repository {
      image_identifier      = "${aws_ecr_repository.app.repository_url}:prod"
      image_repository_type = "ECR"
      image_configuration {
        port = "8000"
        runtime_environment_variables = {
          COGNITO_REGION        = var.region
          COGNITO_USER_POOL_ID  = aws_cognito_user_pool.main.id
          COGNITO_APP_CLIENT_ID = aws_cognito_user_pool_client.web.id
          SQUARE_ENVIRONMENT    = var.square_environment
          SQUARE_APPLICATION_ID = var.square_application_id
          SQUARE_WEBHOOK_URL    = "${local.app_url}/v1/webhooks/square"
          PUBLIC_BASE_URL       = local.app_url
          JOBS_ENABLED          = "true"
        }
        runtime_environment_secrets = local.runtime_secret_arns
      }
    }
  }

  instance_configuration {
    cpu               = "256" # 0.25 vCPU
    memory            = "512" # 0.5 GB
    instance_role_arn = aws_iam_role.apprunner_instance.arn
  }

  network_configuration {
    egress_configuration {
      egress_type       = "VPC"
      vpc_connector_arn = aws_apprunner_vpc_connector.main.arn
    }
    ingress_configuration {
      is_publicly_accessible = true
    }
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = "/v1/health"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 5
  }

  auto_scaling_configuration_arn = aws_apprunner_auto_scaling_configuration_version.main.arn
}
