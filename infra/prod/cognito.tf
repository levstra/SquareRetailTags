resource "aws_cognito_user_pool" "main" {
  name                     = local.name
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  deletion_protection      = "ACTIVE"

  admin_create_user_config {
    allow_admin_create_user_only = true # invite-only: operator CLI and owners
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

resource "aws_cognito_user_pool_client" "web" {
  name                                 = "${local.name}-web"
  user_pool_id                         = aws_cognito_user_pool.main.id
  generate_secret                      = false
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email"]
  allowed_oauth_flows_user_pool_client = true
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = ["${local.app_url}/auth/callback"]
  logout_urls                          = ["${local.app_url}/"]
  explicit_auth_flows                  = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors        = "ENABLED"
  access_token_validity                = 60
  id_token_validity                    = 60
  refresh_token_validity               = 30
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

resource "aws_cognito_user_pool_domain" "main" {
  domain                = var.cognito_domain_prefix
  user_pool_id          = aws_cognito_user_pool.main.id
  managed_login_version = 2 # branded managed login; 1 is the classic hosted UI
}

# Sign-in on our own host. The prefix domain above stays as a fallback (a pool
# may have one of each). Cognito checks that the parent domain resolves, hence
# the dependency on the apex record in site.tf.
resource "aws_cognito_user_pool_domain" "login" {
  count                 = var.deploy_service ? 1 : 0
  domain                = local.login_host
  certificate_arn       = aws_acm_certificate_validation.edge[0].certificate_arn
  user_pool_id          = aws_cognito_user_pool.main.id
  managed_login_version = 2
  depends_on            = [aws_route53_record.apex]
}

resource "aws_route53_record" "login" {
  for_each = var.deploy_service ? toset(["A", "AAAA"]) : toset([])
  zone_id  = data.aws_route53_zone.main[0].zone_id
  name     = local.login_host
  type     = each.key
  alias {
    name                   = aws_cognito_user_pool_domain.login[0].cloudfront_distribution
    zone_id                = aws_cognito_user_pool_domain.login[0].cloudfront_distribution_zone_id
    evaluate_target_health = false
  }
}

# Sign-in page styling. Colors are the tokens in frontend/src/index.css (light
# and dark), as RRGGBBAA; DYNAMIC follows the OS like the app's "system" theme.
# Keys left out of settings.json take Cognito's defaults.
resource "aws_cognito_managed_login_branding" "web" {
  user_pool_id = aws_cognito_user_pool.main.id
  client_id    = aws_cognito_user_pool_client.web.id
  settings     = jsonencode(jsondecode(file("${path.module}/branding/settings.json")))

  # The app's favicon (frontend/public/favicon.svg), padded square because
  # Cognito rejects any other ratio. Every format and mode is set: any left out
  # shows Cognito's default icon. No logo: settings.json disables it.
  dynamic "asset" {
    for_each = setproduct(["LIGHT", "DARK"], ["SVG", "ICO"])
    content {
      category   = "FAVICON_${asset.value[1]}"
      color_mode = asset.value[0]
      extension  = asset.value[1]
      bytes      = filebase64("${path.module}/branding/favicon.${lower(asset.value[1])}")
    }
  }
}

locals {
  cognito_authority  = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
  cognito_domain_url = var.deploy_service ? "https://${local.login_host}" : "https://${var.cognito_domain_prefix}.auth.${var.region}.amazoncognito.com"
}
