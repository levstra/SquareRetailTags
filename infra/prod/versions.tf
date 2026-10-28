terraform {
  required_version = ">= 1.11.0"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  backend "s3" {} # configured with -backend-config=backend.hcl
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "esl", Environment = var.environment, ManagedBy = "terraform" }
  }
}

# CloudFront only accepts ACM certificates from us-east-1 (site.tf, cognito.tf).
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
  default_tags {
    tags = { Project = "esl", Environment = var.environment, ManagedBy = "terraform" }
  }
}

data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name       = "esl-${var.environment}"
  app_host   = "${var.app_subdomain}.${var.hosted_zone_name}"
  app_url    = "https://${local.app_host}"
  login_host = "${var.login_subdomain}.${var.hosted_zone_name}"
  azs        = slice(data.aws_availability_zones.available.names, 0, 2)
  account_id = data.aws_caller_identity.current.account_id
}
