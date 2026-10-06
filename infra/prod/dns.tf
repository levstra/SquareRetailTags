data "aws_route53_zone" "main" {
  count        = var.deploy_service ? 1 : 0
  name         = var.hosted_zone_name
  private_zone = false
}

resource "aws_apprunner_custom_domain_association" "app" {
  count                = var.deploy_service ? 1 : 0
  service_arn          = aws_apprunner_service.app[0].arn
  domain_name          = local.app_host
  enable_www_subdomain = false
}

resource "aws_route53_record" "app" {
  count   = var.deploy_service ? 1 : 0
  zone_id = data.aws_route53_zone.main[0].zone_id
  name    = local.app_host
  type    = "CNAME"
  ttl     = 300
  records = [aws_apprunner_custom_domain_association.app[0].dns_target]
}

# Keys are only known after the association exists: on first deploy, apply
# with -target=aws_apprunner_custom_domain_association.app first (docs/deploy.md).
resource "aws_route53_record" "app_certificate_validation" {
  for_each = {
    for r in try(aws_apprunner_custom_domain_association.app[0].certificate_validation_records, []) : r.name => r
  }
  zone_id = data.aws_route53_zone.main[0].zone_id
  name    = each.value.name
  type    = each.value.type
  ttl     = 300
  records = [each.value.value]
}
