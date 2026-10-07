# ─── GPU alarms (M2a §4f) ───────────────────────────────────────────────────
# Alarms on one email topic (confirm the subscription email once after the first apply):
# - idle worker: detects and emails (M3b §6b). A worker in service while the job queue sees no
#   message received and none deleted for 90 minutes; the circuit breaker (scaling.tf), on its
#   5-minute schedule, then removes protection and sets the group to 0/0/0 unless a warm hold is on.
# - long-running: email only, after 3 hours with a worker in service.
# - one error alarm per Lambda: email on any failed run.

locals {
  # Named here: the breaker reads the alarm by this name.
  idle_alarm = "neurolens-worker-idle"
}

resource "aws_sns_topic" "alerts" {
  name = "neurolens-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "worker_running_long" {
  alarm_name        = "neurolens-worker-running-3h"
  alarm_description = "A NeuroLens GPU worker has been running for 3 hours (warning only; the idle alarm ends idle workers). Check it, or run infra/stop_work.sh."

  namespace   = "AWS/AutoScaling"
  metric_name = "GroupInServiceInstances"
  dimensions  = { AutoScalingGroupName = local.worker_asg }
  statistic   = "Maximum"

  # A worker in service in at least 34 of the last 36 five-minute slices: about 3 hours of GPU time
  # (170 of 180 minutes). A short gap does not make those hours cheaper, so it must not reset the
  # warning; with 36 of 36, a gap near 5 minutes made the alarm flip every 5 minutes (the alarm
  # re-checks every minute and the slices are cut at a different minute each time), one email per
  # flip (M2b CPU rehearsal, build log).
  period              = 300
  evaluation_periods  = 36
  datapoints_to_alarm = 34
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching" # no group yet, or metrics off: no alarm

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# A worker always picks up or finishes a job within 90 minutes when healthy (longest job about
# 55 minutes; scale-in ends an idle worker after 15). So no queue activity for 90 minutes with a
# worker in service means a crash loop, broken code, a frozen job, no NAT Gateway or a warm hold
# (M2b §2d; the hold keeps its worker and the breaker skips it).
# Missing SQS data (queues stop publishing after ~6 idle hours) counts as no activity; missing
# group data never fires it. Manual work needs a warm hold (start_work.sh --keep-worker), not a pause:
# scale-in would end the worker 15 minutes after the queue empties anyway.
# workers_to_zero is scale-in's action (scaling.tf); the idle alarm no longer uses it.
resource "aws_autoscaling_policy" "workers_to_zero" {
  count = local.workers_enabled ? 1 : 0

  name                   = "neurolens-workers-to-zero"
  autoscaling_group_name = aws_autoscaling_group.workers[0].name
  policy_type            = "SimpleScaling"
  adjustment_type        = "ExactCapacity"
  scaling_adjustment     = 0
}

resource "aws_cloudwatch_metric_alarm" "worker_idle" {
  count = local.workers_enabled ? 1 : 0

  alarm_name = local.idle_alarm
  # The email text. Causes: a crash loop, broken code, a frozen job, no NAT Gateway, or a warm hold
  # with no jobs (then the breaker leaves it alone).
  alarm_description = "A NeuroLens worker has been in service for 90 minutes without queue activity. Unless a warm hold is on, the circuit breaker stops all workers (max 0) within 5 minutes. Run infra/start_work.sh to start again."

  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 18 # 18 x 5 min = 90 minutes
  datapoints_to_alarm = 18
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "idle"
    expression  = "IF(insvc > 0 AND FILL(recv, 0) + FILL(del, 0) == 0, 1, 0)"
    label       = "Worker in service with no queue activity"
    return_data = true
  }
  metric_query {
    id = "insvc"
    metric {
      namespace   = "AWS/AutoScaling"
      metric_name = "GroupInServiceInstances"
      dimensions  = { AutoScalingGroupName = local.worker_asg }
      period      = 300
      stat        = "Maximum"
    }
  }
  metric_query {
    id = "recv"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "NumberOfMessagesReceived"
      dimensions  = { QueueName = aws_sqs_queue.jobs.name }
      period      = 300
      stat        = "Sum"
    }
  }
  metric_query {
    id = "del"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "NumberOfMessagesDeleted"
      dimensions  = { QueueName = aws_sqs_queue.jobs.name }
      period      = 300
      stat        = "Sum"
    }
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# ─── One error alarm per Lambda (M3b §6b) ──────────────────────────────────
# Catches a function that runs and fails, not one that never runs. The dead-letter handler also errors,
# by design, when a worker still holds a job (a rare duplicate message): one such email is expected;
# repeated ones are not.

locals {
  lambda_error_alarms = merge(
    local.workers_enabled ? {
      "neurolens-breaker" = "Until it works, only the 3-hour email covers a broken worker. Run infra/stop_work.sh if in doubt."
    } : {},
    { for k, v in local.m3a_lambdas : v.name => "Jobs may be left unsettled." },
    {
      (local.web_lambdas.web.name)     = "The website may be failing for users."
      (local.web_lambdas.webhook.name) = "A paid Stripe test top-up may not have been credited (Stripe retries for 3 days)."
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

# The breaker's and M3a's alarms keep their names and move into the block above.
moved {
  from = aws_cloudwatch_metric_alarm.breaker_errors[0]
  to   = aws_cloudwatch_metric_alarm.lambda_errors["neurolens-breaker"]
}

moved {
  from = aws_cloudwatch_metric_alarm.m3a_lambda_errors["dlq"]
  to   = aws_cloudwatch_metric_alarm.lambda_errors["neurolens-dlq-handler"]
}

moved {
  from = aws_cloudwatch_metric_alarm.m3a_lambda_errors["reaper"]
  to   = aws_cloudwatch_metric_alarm.lambda_errors["neurolens-reaper"]
}
