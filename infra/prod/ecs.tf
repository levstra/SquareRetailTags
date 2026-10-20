resource "aws_ecs_cluster" "main" {
  name = local.name
}

resource "aws_cloudwatch_log_group" "tasks" {
  name              = "/esl/${var.environment}/tasks"
  retention_in_days = 30
}

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "${local.name}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "task_execution_secrets" {
  name = "read-task-secrets"
  role = aws_iam_role.task_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue"]
      Resource = [
        aws_secretsmanager_secret.database_url.arn,
        aws_secretsmanager_secret.admin_database_url.arn,
        aws_secretsmanager_secret.db_admin_password.arn,
        aws_secretsmanager_secret.db_app_password.arn,
        aws_secretsmanager_secret.db_readonly_password.arn,
        aws_db_instance.main.master_user_secret[0].secret_arn,
      ]
    }]
  })
}

# What `esl admin ...` itself may call (Cognito invites for create-business / invite-user).
resource "aws_iam_role" "admin_task" {
  name               = "${local.name}-admin-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "admin_task_cognito" {
  name = "cognito-admin"
  role = aws_iam_role.admin_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["cognito-idp:AdminCreateUser", "cognito-idp:AdminDeleteUser", "cognito-idp:AdminDisableUser", "cognito-idp:AdminEnableUser"]
      Resource = aws_cognito_user_pool.main.arn
    }]
  })
}

locals {
  task_environment = [
    { name = "COGNITO_REGION", value = var.region },
    { name = "COGNITO_USER_POOL_ID", value = aws_cognito_user_pool.main.id },
    { name = "COGNITO_APP_CLIENT_ID", value = aws_cognito_user_pool_client.web.id },
    { name = "SQUARE_ENVIRONMENT", value = var.square_environment },
    { name = "JOBS_ENABLED", value = "false" },
  ]
  # esl_admin credentials: only these task definitions ever receive them.
  admin_db_secrets = [
    { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn },
    { name = "ADMIN_DATABASE_URL", valueFrom = aws_secretsmanager_secret.admin_database_url.arn },
  ]
  task_common = {
    requires_compatibilities = ["FARGATE"]
    network_mode             = "awsvpc"
    cpu                      = "256"
    memory                   = "512"
  }
}

resource "aws_ecs_task_definition" "admin" {
  family                   = "${local.name}-admin"
  requires_compatibilities = local.task_common.requires_compatibilities
  network_mode             = local.task_common.network_mode
  cpu                      = local.task_common.cpu
  memory                   = local.task_common.memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.admin_task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name        = "esl"
    image       = "${aws_ecr_repository.app.repository_url}:prod"
    essential   = true
    command     = ["esl", "admin", "--help"]
    environment = local.task_environment
    secrets     = local.admin_db_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.tasks.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "admin"
      }
    }
  }])
}

# Runs the NEW image (:candidate) before it becomes :prod.
resource "aws_ecs_task_definition" "migrate" {
  family                   = "${local.name}-migrate"
  requires_compatibilities = local.task_common.requires_compatibilities
  network_mode             = local.task_common.network_mode
  cpu                      = local.task_common.cpu
  memory                   = local.task_common.memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name        = "esl"
    image       = "${aws_ecr_repository.app.repository_url}:candidate"
    essential   = true
    command     = ["alembic", "upgrade", "head"]
    environment = local.task_environment
    secrets     = local.admin_db_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.tasks.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "migrate"
      }
    }
  }])
}

# One-time: create esl_admin / esl_app / esl_readonly on the fresh instance
# (re-run to add esl_readonly to an existing one). Only place the RDS master
# secret is injected.
resource "aws_ecs_task_definition" "bootstrap" {
  family                   = "${local.name}-bootstrap"
  requires_compatibilities = local.task_common.requires_compatibilities
  network_mode             = local.task_common.network_mode
  cpu                      = local.task_common.cpu
  memory                   = local.task_common.memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name      = "esl"
    image     = "${aws_ecr_repository.app.repository_url}:prod"
    essential = true
    command   = ["esl", "admin", "bootstrap-db"]
    environment = concat(local.task_environment, [
      { name = "DB_HOST", value = aws_db_instance.main.address },
      { name = "DB_PORT", value = tostring(aws_db_instance.main.port) },
      { name = "DB_NAME", value = "esl" },
    ])
    secrets = [
      { name = "DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn },
      { name = "MASTER_DB_USER", valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:username::" },
      { name = "MASTER_DB_PASSWORD", valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:password::" },
      { name = "ESL_ADMIN_PASSWORD", valueFrom = aws_secretsmanager_secret.db_admin_password.arn },
      { name = "ESL_APP_PASSWORD", valueFrom = aws_secretsmanager_secret.db_app_password.arn },
      { name = "ESL_READONLY_PASSWORD", valueFrom = aws_secretsmanager_secret.db_readonly_password.arn },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.tasks.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "bootstrap"
      }
    }
  }])
}
