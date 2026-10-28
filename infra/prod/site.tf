# The bare domain (levstra.com). Cognito refuses a custom domain whose parent
# has no A record, so login.<zone> needs this to exist.
#
# Today every request is redirected to the app, except /email/* (images for
# Cognito email, email.tf), which is served from aws_s3_bucket.site. For a
# landing page: upload it to the bucket (index.html at the root) and remove the
# default behavior's function_association. The redirect is a 302 so browsers
# don't cache it.

resource "aws_acm_certificate" "edge" {
  count                     = var.deploy_service ? 1 : 0
  provider                  = aws.us_east_1
  domain_name               = var.hosted_zone_name
  subject_alternative_names = [local.login_host]
  validation_method         = "DNS"
  lifecycle {
    create_before_destroy = true
  }
}

# Keyed by the domain names, which are known at plan time; the records
# themselves aren't until the certificate exists.
locals {
  edge_validation = {
    for o in try(aws_acm_certificate.edge[0].domain_validation_options, []) : o.domain_name => o
  }
}

resource "aws_route53_record" "edge_certificate_validation" {
  for_each        = var.deploy_service ? toset([var.hosted_zone_name, local.login_host]) : toset([])
  zone_id         = data.aws_route53_zone.main[0].zone_id
  name            = local.edge_validation[each.key].resource_record_name
  type            = local.edge_validation[each.key].resource_record_type
  ttl             = 300
  records         = [local.edge_validation[each.key].resource_record_value]
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "edge" {
  count                   = var.deploy_service ? 1 : 0
  provider                = aws.us_east_1
  certificate_arn         = aws_acm_certificate.edge[0].arn
  validation_record_fqdns = [for r in aws_route53_record.edge_certificate_validation : r.fqdn]
}

resource "aws_s3_bucket" "site" {
  count  = var.deploy_service ? 1 : 0
  bucket = "${local.name}-site-${local.account_id}"
}

resource "aws_s3_bucket_public_access_block" "site" {
  count                   = var.deploy_service ? 1 : 0
  bucket                  = aws_s3_bucket.site[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "site" {
  count                             = var.deploy_service ? 1 : 0
  name                              = "${local.name}-site"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_s3_bucket_policy" "site" {
  count  = var.deploy_service ? 1 : 0
  bucket = aws_s3_bucket.site[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.site[0].arn}/*"
      Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.site[0].arn } }
    }]
  })
}

resource "aws_cloudfront_function" "apex_redirect" {
  count   = var.deploy_service ? 1 : 0
  name    = "${local.name}-apex-redirect"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = <<-EOT
    function handler(event) {
      return {
        statusCode: 302,
        statusDescription: "Found",
        headers: { location: { value: "${local.app_url}/" } },
      };
    }
  EOT
}

resource "aws_cloudfront_distribution" "site" {
  count               = var.deploy_service ? 1 : 0
  enabled             = true
  is_ipv6_enabled     = true
  aliases             = [var.hosted_zone_name]
  default_root_object = "index.html"
  price_class         = "PriceClass_100"

  origin {
    origin_id                = "site"
    domain_name              = aws_s3_bucket.site[0].bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.site[0].id
  }

  default_cache_behavior {
    target_origin_id       = "site"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = "658327ea-f89d-4fab-a63d-7e88639e58f6" # Managed-CachingOptimized
    compress               = true

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.apex_redirect[0].arn
    }
  }

  ordered_cache_behavior {
    path_pattern           = "/email/*"
    target_origin_id       = "site"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = "658327ea-f89d-4fab-a63d-7e88639e58f6" # Managed-CachingOptimized
    compress               = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.edge[0].certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }
}

resource "aws_route53_record" "apex" {
  for_each = var.deploy_service ? toset(["A", "AAAA"]) : toset([])
  zone_id  = data.aws_route53_zone.main[0].zone_id
  name     = var.hosted_zone_name
  type     = each.key
  alias {
    name                   = aws_cloudfront_distribution.site[0].domain_name
    zone_id                = aws_cloudfront_distribution.site[0].hosted_zone_id
    evaluate_target_health = false
  }
}
