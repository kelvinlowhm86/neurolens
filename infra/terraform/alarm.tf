# ─── Long-running GPU alarm (M2a §4f) ──────────────────────────────────────
# Emails if a worker has been running for 3 hours straight: the backstop for what self-termination
# cannot catch (a worker stuck mid-job, an unreachable NAT instance). Confirm the subscription email
# once after the first apply. About $0.10 a month.

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
  alarm_description = "A NeuroLens GPU worker has been running for 3 hours. Check it, or run infra/stop_work.sh."

  namespace   = "AWS/AutoScaling"
  metric_name = "GroupInServiceInstances"
  dimensions  = { AutoScalingGroupName = local.worker_asg }
  statistic   = "Maximum"

  period              = 300
  evaluation_periods  = 36 # 36 x 5 min = 3 hours
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching" # no group yet, or metrics off: no alarm

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}
