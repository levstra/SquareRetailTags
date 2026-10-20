resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone = local.azs[count.index]
  tags              = { Name = "${local.name}-public-${count.index}" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  availability_zone = local.azs[count.index]
  tags              = { Name = "${local.name}-private-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# App Runner (via its VPC connector), RDS and Fargate tasks live here. With a
# VPC connector attached, App Runner sends ALL outbound app traffic through the
# VPC, so Square and Cognito JWKS go through the NAT instance. Fargate tasks
# pull from ECR through the NAT + S3 endpoint.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-private" }
}

resource "aws_route" "private_default_via_nat" {
  route_table_id         = aws_route_table.private.id
  destination_cidr_block = "0.0.0.0/0"
  network_interface_id   = aws_instance.nat.primary_network_interface_id
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# Free: ECR image layers come from S3, so pulls skip the NAT instance.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
}

resource "aws_security_group" "apprunner" {
  name        = "${local.name}-apprunner"
  description = "App Runner VPC connector"
  vpc_id      = aws_vpc.main.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "tasks" {
  name        = "${local.name}-tasks"
  description = "One-off Fargate tasks (migrations, admin CLI)"
  vpc_id      = aws_vpc.main.id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "rds" {
  name        = "${local.name}-rds"
  description = "Postgres from App Runner and Fargate tasks only"
  vpc_id      = aws_vpc.main.id
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.apprunner.id, aws_security_group.tasks.id]
  }
  # Operator SQL: Session Manager port forwarding through the NAT instance
  # (docs/deploy.md, "Querying the database"). A separate block so adding it
  # leaves the App Runner/Fargate rule untouched.
  ingress {
    description     = "Operator psql via SSM port forward on the NAT"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.nat.id]
  }
}

resource "aws_subnet" "rds_extra" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, 12)
  availability_zone = "${var.region}c"
  tags              = { Name = "${local.name}-rds-extra" }
}

resource "aws_route_table_association" "rds_extra" {
  subnet_id      = aws_subnet.rds_extra.id
  route_table_id = aws_route_table.private.id
}