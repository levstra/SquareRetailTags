data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_security_group" "nat" {
  name        = "${local.name}-nat"
  description = "NAT instance: forward anything from the VPC"
  vpc_id      = aws_vpc.main.id
  ingress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_iam_role" "nat" {
  name = "${local.name}-nat"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

# Session Manager access for debugging; no SSH keys or open ports.
resource "aws_iam_role_policy_attachment" "nat_ssm" {
  role       = aws_iam_role.nat.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "nat" {
  name = "${local.name}-nat"
  role = aws_iam_role.nat.name
}

resource "aws_instance" "nat" {
  ami                         = data.aws_ssm_parameter.al2023_arm64.value
  instance_type               = "t4g.nano"
  subnet_id                   = aws_subnet.public[0].id
  vpc_security_group_ids      = [aws_security_group.nat.id]
  source_dest_check           = false # required to forward traffic it doesn't own
  iam_instance_profile        = aws_iam_instance_profile.nat.name
  associate_public_ip_address = true # needed at boot, before the EIP associates
  user_data                   = replace(templatefile("${path.module}/nat-user-data.sh", { vpc_cidr = var.vpc_cidr }), "\r\n", "\n")
  user_data_replace_on_change = true
  metadata_options {
    http_tokens = "required"
  }
  lifecycle {
    ignore_changes = [ami] # don't replace the NAT on every new AL2023 release
  }
  tags = { Name = "${local.name}-nat" }
}

resource "aws_eip" "nat" {
  domain   = "vpc"
  instance = aws_instance.nat.id
  tags     = { Name = "${local.name}-nat" }
}

# Free auto-recovery if the underlying host fails.
resource "aws_cloudwatch_metric_alarm" "nat_recover" {
  alarm_name          = "${local.name}-nat-recover"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_System"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  dimensions          = { InstanceId = aws_instance.nat.id }
  alarm_actions       = ["arn:aws:automate:${var.region}:ec2:recover", aws_sns_topic.alarms.arn]
}
