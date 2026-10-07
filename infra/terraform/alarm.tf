# ─── GPU alarms (M2a §4f) ───────────────────────────────────────────────────
# Two alarms on one email topic (confirm the subscription email once after the first apply):
# - idle worker: ACTS. A worker in service while the job queue sees no message received and none
#   deleted for 90 minutes is ended by AWS itself (group desired capacity to 0), and the circuit
#   breaker (scaling.tf) sets the group's max to 0 so it is not replaced.
# - long-running: email only, after 3 hours with a worker in service.
# About $0.40 a month for both (four alarm metrics).

locals {
  # Named here, not read from the alarm: the alarm runs the breaker, which needs the name (a cycle).
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
# worker in service means a crash loop, broken code, a frozen job, a dead NAT instance or a warm
# hold (M2b §2d; the hold keeps its worker and the breaker skips it).
# Missing SQS data (queues stop publishing after ~6 idle hours) counts as no activity; missing
# group data never fires it. Manual work needs a warm hold (start_work.sh --worker), not a pause:
# scale-in would end the worker 15 minutes after the queue empties anyway.
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
  # The email text (M2b §2c). Causes: a crash loop, broken code, a frozen job or a down NAT
  # instance, or a warm hold with no jobs (then it only emails).
  alarm_description = "AWS has stopped all NeuroLens workers (max 0) after 90 minutes without queue activity, unless a warm hold is active. Run infra/start_work.sh to start again."

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

  # The breaker runs at once as well (M2b §2c): the "set to 0" policy ends the worker, and with a job
  # waiting, scale-out would launch a replacement about a minute later, before a scheduled run set
  # max 0 (seen in the CPU rehearsal, R8).
  alarm_actions = [
    aws_autoscaling_policy.workers_to_zero[0].arn,
    aws_sns_topic.alerts.arn,
    aws_lambda_function.breaker[0].arn,
  ]
  ok_actions = [aws_sns_topic.alerts.arn]
}
