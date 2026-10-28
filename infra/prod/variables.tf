variable "region" {
  type    = string
  default = "us-west-2"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "hosted_zone_name" {
  type        = string
  description = "Existing public Route 53 hosted zone, e.g. example.com."
}

variable "app_subdomain" {
  type    = string
  default = "app"
}

variable "login_subdomain" {
  type        = string
  default     = "login"
  description = "Cognito sign-in host, e.g. login.example.com. Created with deploy_service."
}

variable "cognito_domain_prefix" {
  type        = string
  description = "Globally unique prefix for <prefix>.auth.<region>.amazoncognito.com."
  validation {
    condition     = can(regex("^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", var.cognito_domain_prefix)) && !can(regex("aws|amazon|cognito", var.cognito_domain_prefix))
    error_message = "cognito_domain_prefix must be lowercase letters, digits and hyphens (not starting or ending with a hyphen) and must not contain aws, amazon or cognito."
  }
}

variable "alarm_email" {
  type        = string
  description = "Receives CloudWatch alarm emails (confirm the SNS subscription email)."
}

variable "github_oidc_subject_prefix" {
  type        = string
  default     = "repo:levstra@339920851/SquareRetailTags@1336255242"
  description = <<-EOT
    The repo's OIDC `sub` prefix, exactly as GitHub issues it. The repo uses immutable subjects
    (owner and repo IDs), so renames and transfers don't break the trust. Check it with:
    gh api repos/levstra/SquareRetailTags/actions/oidc/customization/sub (sub_claim_prefix).
  EOT
}

variable "create_github_oidc_provider" {
  type        = bool
  default     = true
  description = "False if this AWS account already has token.actions.githubusercontent.com."
}

variable "deploy_service" {
  type        = bool
  default     = false
  description = "Create App Runner + custom domain. Leave false until an image is in ECR and secrets have values."
}

variable "square_environment" {
  type    = string
  default = "PRODUCTION"
}

variable "square_application_id" {
  type        = string
  default     = ""
  description = "Square production application ID (not secret)."
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}
