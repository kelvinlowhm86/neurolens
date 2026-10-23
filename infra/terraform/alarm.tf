# ─── GPU and Lambda alarms (M3e §6c, §9) ──────────────────────────────────
# One email topic (confirm the subscription email once after the first apply):
# - the GPU guard stopped a GPU that scale-in should have stopped: a person should look;
# - the GPU guard has not run for 30 minutes (EventBridge switched off or removed);
# - Aurora has been awake for much of 6 hours;
# - one error alarm per Lambda: email on any failed run.

resource "aws_sns_topic" "alerts" {
  name = "neurolens-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Forgotten-database alarm (M3b §6): awake in more than 30% of the minutes of each of 6 hours in a
# row means something keeps it awake (a forgotten stop_work.sh, an open tab, a retrying Lambda):
# about $1.40 a day at 0.5 ACU. The metric reads 0 while paused, so the hourly 70th percentile of
# its per-minute values is above 0 exactly when it was awake for over 30% of the hour, whatever
# capacity it ran at. A reaper wake (about 11 minutes, measured) stays below that; a caller every
# 12 minutes (about 45%) and a held minimum (100%) are above it.
# ─── GPU guard alarms (M3e §6c) ───────────────────────────────────────────

# The guard logs GPU_GUARD_STOPPED only when scale-in did not stop the GPU. The alarm emails so a
# person can look at the other signal (scale-in) while the guard keeps the spend bounded.
resource "aws_cloudwatch_log_metric_filter" "gpu_guard_stopped" {
  name           = "neurolens-gpu-guard-stopped"
  log_group_name = aws_cloudwatch_log_group.gpu_guard.name
  pattern        = "GPU_GUARD_STOPPED"

  metric_transformation {
    name      = "GpuGuardStopped"
    namespace = "NeuroLens"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "gpu_guard_stopped" {
  alarm_name        = "neurolens-gpu-guard-stopped"
  alarm_description = "The NeuroLens GPU guard stopped a GPU (scale-in did not). Look for why the backlog metric stayed above zero; the guard keeps the spend bounded."

  namespace   = "NeuroLens"
  metric_name = "GpuGuardStopped"
  statistic   = "Sum"

  period              = 600
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
  tags          = { Milestone = "M3e" }
}

# The error alarm above catches a run that fails, not one that never happens. Lambda publishes no
# Invocations datapoint in a period with none, so missing data counts as breaching.
resource "aws_cloudwatch_metric_alarm" "gpu_guard_not_running" {
  alarm_name        = "neurolens-gpu-guard-not-running"
  alarm_description = "The NeuroLens GPU guard has not run for 30 minutes (it should run every 10). Nothing would stop a GPU left on with nothing to do. Check the neurolens-gpu-guard-every-10-min rule is ENABLED; run infra/stop_work.sh if in doubt."

  namespace   = "AWS/Lambda"
  metric_name = "Invocations"
  dimensions  = { FunctionName = aws_lambda_function.gpu_guard.function_name }
  statistic   = "Sum"

  period              = 1800
  evaluation_periods  = 1
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
  tags          = { Milestone = "M3e" }
}

# ─── One error alarm per Lambda (M3b §6b) ──────────────────────────────────
# Catches a function that runs and fails, not one that never runs. The dead-letter handler and
# the error handler also error, by design, while a container still holds a job (a rare duplicate
# message): one such email is expected; repeated ones are not.

locals {
  lambda_error_alarms = merge(
    { for k, v in local.m3a_lambdas : v.name => "Jobs may be left unsettled." },
    {
      (aws_lambda_function.dispatcher.function_name) = "Uploads may wait and never reach the GPU."
      (aws_lambda_function.gpu_guard.function_name)  = "A GPU left on would only be stopped by scale-in."
      (local.web_lambdas.web.name)                   = "The website may be failing for users."
    },
  )
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = local.lambda_error_alarms

  alarm_name        = "${each.key}-errors"
  alarm_description = "The NeuroLens ${each.key} Lambda failed. ${each.value} Check its log (/aws/lambda/${each.key})."

  namespace   = "AWS/Lambda"
  metric_name = "Errors"
  dimensions  = { FunctionName = each.key }
  statistic   = "Sum"

  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  tags          = { Milestone = "M3b" }
}

# A Cognito account deletion whose login removal failed (M3c §5). The route logs
# COGNITO_DELETE_FAILED and answers 500 login_delete_failed, so the function itself does not fail
# and the Lambda error alarm above never fires. The operator deletes the login by hand.
resource "aws_cloudwatch_log_metric_filter" "cognito_delete_failed" {
  name           = "neurolens-cognito-delete-failed"
  log_group_name = aws_cloudwatch_log_group.web["web"].name
  pattern        = "COGNITO_DELETE_FAILED"

  metric_transformation {
    name      = "CognitoDeleteFailed"
    namespace = "NeuroLens"
    value     = "1"
  }
}

resource "aws_cloudwatch_metric_alarm" "cognito_delete_failed" {
  alarm_name        = "neurolens-cognito-delete-failed"
  alarm_description = "Removing a deleted account's Cognito login failed. The user's data is gone; delete the login by hand. Its sub is in the COGNITO_DELETE_FAILED log line (/aws/lambda/${local.web_lambdas.web.name})."

  namespace   = "NeuroLens"
  metric_name = "CognitoDeleteFailed"
  statistic   = "Sum"

  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
  tags          = { Milestone = "M3c" }
}
