# ─── Autoscaling on the job queue and the circuit breaker (M2b §2) ─────────
# From M2b only AWS decides how many workers run (the worker never ends its own machine):
# - scale out: a job waiting in the queue adds a worker, up to the group's max (1, or 2 for
#   Experiment 2). Outside a session the max is 0, so an upload waits for the next session.
# - scale in: queue empty (waiting and in-progress jobs) for 15 minutes sets the group to 0.
# - circuit breaker: every 5 minutes a Lambda checks the idle alarm (alarm.tf); while it is in
#   ALARM and no warm hold is on, it sets the group to max 0, so a broken worker is not replaced in
#   a loop. Nothing launches again until start_work.sh.
# A busy worker holds scale-in protection (neurolens/worker.py), so scale-in never ends it mid-job;
# the breaker and stop_work.sh remove that protection when they stop the group.
# Alarms about $0.50 a month; the Lambda's ~9,000 runs a month are inside the free tier ($0).

# ─── Scale out ─────────────────────────────────────────────────────────────

resource "aws_autoscaling_policy" "scale_out" {
  count = local.workers_enabled ? 1 : 0

  name                      = "neurolens-workers-scale-out"
  autoscaling_group_name    = aws_autoscaling_group.workers[0].name
  policy_type               = "StepScaling"
  adjustment_type           = "ChangeInCapacity"
  metric_aggregation_type   = "Maximum"
  estimated_instance_warmup = var.scale_out_warmup_seconds

  # Bounds are relative to the alarm's threshold (1 waiting job). During a warm-up, repeated
  # breaches in the same step add nothing more, so a backlog gets its second worker from the
  # second step at once (capped by the group's max), not by repetition.
  step_adjustment {
    metric_interval_lower_bound = 0
    metric_interval_upper_bound = 1
    scaling_adjustment          = 1
  }
  step_adjustment {
    metric_interval_lower_bound = 1
    scaling_adjustment          = 2
  }
}

resource "aws_cloudwatch_metric_alarm" "scale_out" {
  count = local.workers_enabled ? 1 : 0

  alarm_name        = "neurolens-worker-scale-out" # experiments/cold_start.py reads its history
  alarm_description = "A NeuroLens job is waiting in the queue: add a worker (up to the group's max)."

  namespace   = "AWS/SQS"
  metric_name = "ApproximateNumberOfMessagesVisible"
  dimensions  = { QueueName = aws_sqs_queue.jobs.name }
  statistic   = "Maximum"

  period              = 60
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching" # a quiet queue publishes nothing: nothing to add

  alarm_actions = [aws_autoscaling_policy.scale_out[0].arn]
  tags          = { Milestone = "M2b" }
}

# ─── Scale in ──────────────────────────────────────────────────────────────
# Waiting AND in-progress messages: a running job's message is invisible, so scaling on waiting
# messages alone would see an empty queue mid-job. The metrics arrive 1-3 minutes late, so a job
# taken in the last minutes can still look "empty": the worker's scale-in protection covers that.

resource "aws_cloudwatch_metric_alarm" "scale_in" {
  count = local.workers_enabled ? 1 : 0

  alarm_name        = "neurolens-worker-scale-in"
  alarm_description = "The NeuroLens job queue has been empty for 15 minutes: set the worker group to 0 (a warm hold keeps its minimum)."

  comparison_operator = "LessThanOrEqualToThreshold"
  threshold           = 0
  evaluation_periods  = 15
  datapoints_to_alarm = 15
  # After hours of quiet SQS stops publishing these metrics: no data counts as an empty queue.
  treat_missing_data = "breaching"

  metric_query {
    id          = "jobs"
    expression  = "FILL(visible, 0) + FILL(inflight, 0)"
    label       = "Jobs waiting or in progress"
    return_data = true
  }
  metric_query {
    id = "visible"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesVisible"
      dimensions  = { QueueName = aws_sqs_queue.jobs.name }
      period      = 60
      stat        = "Maximum"
    }
  }
  metric_query {
    id = "inflight"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesNotVisible"
      dimensions  = { QueueName = aws_sqs_queue.jobs.name }
      period      = 60
      stat        = "Maximum"
    }
  }

  alarm_actions = [aws_autoscaling_policy.workers_to_zero[0].arn]
  tags          = { Milestone = "M2b" }
}

# ─── Circuit breaker ───────────────────────────────────────────────────────

data "archive_file" "breaker" {
  type        = "zip"
  source_file = "${path.module}/../lambda/breaker.py"
  output_path = "${path.module}/.build/breaker.zip"
}

resource "aws_cloudwatch_log_group" "breaker" {
  count = local.workers_enabled ? 1 : 0

  name              = "/aws/lambda/neurolens-breaker"
  retention_in_days = 30
  tags              = { Milestone = "M2b" }
}

resource "aws_lambda_function" "breaker" {
  count = local.workers_enabled ? 1 : 0

  function_name    = "neurolens-breaker"
  role             = aws_iam_role.breaker.arn
  runtime          = "python3.12"
  handler          = "breaker.handler"
  filename         = data.archive_file.breaker.output_path
  source_code_hash = data.archive_file.breaker.output_base64sha256
  timeout          = 30

  environment {
    variables = {
      WORKER_GROUP = aws_autoscaling_group.workers[0].name
      IDLE_ALARM   = local.idle_alarm
    }
  }

  depends_on = [aws_cloudwatch_log_group.breaker, aws_iam_role_policy.breaker]
  tags       = { Milestone = "M2b" }
}

# Every 5 minutes, its only trigger (M3b §6b): it acts while the idle alarm is in ALARM, so a loop of
# replacements that keeps the alarm in ALARM, or a warm hold ending during one, is still caught.
resource "aws_cloudwatch_event_rule" "breaker_schedule" {
  count = local.workers_enabled ? 1 : 0

  name                = "neurolens-breaker-every-5-min" # start_work.sh checks it is ENABLED
  description         = "Run the NeuroLens circuit breaker (it acts only while the idle alarm is in ALARM)."
  schedule_expression = "rate(5 minutes)"
  tags                = { Milestone = "M2b" }
}

resource "aws_cloudwatch_event_target" "breaker" {
  count = local.workers_enabled ? 1 : 0

  rule = aws_cloudwatch_event_rule.breaker_schedule[0].name
  arn  = aws_lambda_function.breaker[0].arn
}

resource "aws_lambda_permission" "events_invoke_breaker" {
  count = local.workers_enabled ? 1 : 0

  statement_id  = "AllowBreakerSchedule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.breaker[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.breaker_schedule[0].arn
}
