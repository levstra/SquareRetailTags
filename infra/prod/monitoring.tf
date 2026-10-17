resource "aws_sns_topic" "alarms" {
  name = "${local.name}-alarms"
}

resource "aws_sns_topic_subscription" "alarms_email" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "apprunner_5xx" {
  count               = var.deploy_service ? 1 : 0
  alarm_name          = "${local.name}-5xx"
  alarm_description   = "App Runner returned more than 10 server errors in 5 minutes."
  namespace           = "AWS/AppRunner"
  metric_name         = "5xxStatusResponses"
  dimensions          = { ServiceName = aws_apprunner_service.app[0].service_name, ServiceID = aws_apprunner_service.app[0].service_id }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 10
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

# App Runner creates this log group when the service starts.
resource "aws_cloudwatch_log_metric_filter" "sync_failures" {
  count          = var.deploy_service ? 1 : 0
  name           = "${local.name}-square-sync-failures"
  log_group_name = "/aws/apprunner/${aws_apprunner_service.app[0].service_name}/${aws_apprunner_service.app[0].service_id}/application"
  pattern        = "\"square sync failed\"" # esl.observability.SYNC_FAILURE_MARKER
  metric_transformation {
    name          = "SquareSyncFailures"
    namespace     = "ESL/${var.environment}"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "sync_failures" {
  count               = var.deploy_service ? 1 : 0
  alarm_name          = "${local.name}-square-sync-failures"
  alarm_description   = "3+ Square sync failures in an hour. Search the application log for 'square sync failed business=' to find which business."
  namespace           = "ESL/${var.environment}"
  metric_name         = "SquareSyncFailures"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 3
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  depends_on          = [aws_cloudwatch_log_metric_filter.sync_failures]
}

# esl.gateway.reconcile logs (ERROR) "Label update <id> for tag <tag> re-pended
# <n> times without a final report".
resource "aws_cloudwatch_log_metric_filter" "repends" {
  count          = var.deploy_service ? 1 : 0
  name           = "${local.name}-label-repends"
  log_group_name = "/aws/apprunner/${aws_apprunner_service.app[0].service_name}/${aws_apprunner_service.app[0].service_id}/application"
  pattern        = "\"re-pended\""
  metric_transformation {
    name          = "LabelRepends"
    namespace     = "ESL/${var.environment}"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "repends" {
  count               = var.deploy_service ? 1 : 0
  alarm_name          = "${local.name}-label-repends"
  alarm_description   = "A label update was re-pended repeatedly: a Pi accepted commands but never reported back. Search the application log for 're-pended' to find the tag and store."
  namespace           = "ESL/${var.environment}"
  metric_name         = "LabelRepends"
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
  depends_on          = [aws_cloudwatch_log_metric_filter.repends]
}
