# ─── The one-page product dashboard (experiments and demos) ────────────────
#
# One CloudWatch dashboard to watch a run from upload to result and to take screenshots for the
# report. Every widget points at a resource in this stack, so a rename cannot leave it stale.

locals {
  # Every function's errors and slowest run, in a stable order: the job Lambdas, then the rest.
  dashboard_lambdas = concat(
    [for f in aws_lambda_function.m3a : f.function_name],
    [
      aws_lambda_function.dispatcher.function_name,
      aws_lambda_function.gpu_guard.function_name,
      aws_lambda_function.report.function_name,
      aws_lambda_function.web["web"].function_name,
    ],
  )

  # The endpoint's alarms only exist once the image does (count 0 before that).
  dashboard_alarms = concat(
    aws_cloudwatch_metric_alarm.scale_out[*].arn,
    aws_cloudwatch_metric_alarm.scale_in[*].arn,
    [for a in aws_cloudwatch_metric_alarm.lambda_errors : a.arn],
    [
      aws_cloudwatch_metric_alarm.gpu_guard_stopped.arn,
      aws_cloudwatch_metric_alarm.gpu_guard_not_running.arn,
      aws_cloudwatch_metric_alarm.cognito_delete_failed.arn,
      aws_cloudwatch_metric_alarm.db_awake_long.arn,
    ],
  )
}

resource "aws_cloudwatch_dashboard" "neurolens" {
  dashboard_name = "neurolens"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "alarm"
        x      = 0
        y      = 0
        width  = 24
        height = 4
        properties = {
          title  = "Alarms"
          alarms = local.dashboard_alarms
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 4
        width  = 24
        height = 6
        properties = {
          title  = "Jobs"
          region = var.region
          view   = "timeSeries"
          period = 60
          metrics = [
            ["AWS/SQS", "NumberOfMessagesSent", "QueueName", aws_sqs_queue.jobs.name, { stat = "Sum", label = "Uploads", period = 60 }],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.jobs.name, { stat = "Maximum", label = "Waiting jobs", period = 60 }],
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.jobs_dlq.name, { stat = "Maximum", label = "Failed jobs", period = 60 }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 10
        width  = 8
        height = 6
        properties = {
          title  = "GPU backlog and requests with no GPU"
          region = var.region
          view   = "timeSeries"
          period = 60
          metrics = [
            ["AWS/SageMaker", "ApproximateBacklogSize", "EndpointName", local.endpoint_name, { stat = "Maximum", label = "Backlog", period = 60 }],
            ["AWS/SageMaker", "HasBacklogWithoutCapacity", "EndpointName", local.endpoint_name, { stat = "Maximum", label = "Waiting with no GPU", period = 60 }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 8
        y      = 10
        width  = 8
        height = 6
        properties = {
          title  = "GPU jobs finished and failed"
          region = var.region
          view   = "timeSeries"
          period = 60
          metrics = [
            ["AWS/SageMaker", "InvocationsProcessed", "EndpointName", local.endpoint_name, "VariantName", local.variant_name, { stat = "Sum", label = "Finished", period = 60 }],
            ["AWS/SageMaker", "InvocationFailures", "EndpointName", local.endpoint_name, "VariantName", local.variant_name, { stat = "Sum", label = "Failed", period = 60 }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 16
        y      = 10
        width  = 8
        height = 6
        properties = {
          title  = "Time waiting in the backlog (seconds)"
          region = var.region
          view   = "timeSeries"
          period = 60
          metrics = [
            ["AWS/SageMaker", "TimeInBacklog", "EndpointName", local.endpoint_name, "VariantName", local.variant_name, { stat = "Average", id = "wait_ms", visible = false, period = 60 }],
            [{ expression = "wait_ms/1000", label = "Average wait (s)" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 16
        width  = 24
        height = 6
        properties = {
          title  = "Processing time by GPU type (seconds)"
          region = var.region
          view   = "timeSeries"
          period = 60
          # One line per pool from the endpoint configuration, so a slow backup-GPU run stands out.
          # concat, not flatten: flatten would merge every row into one list.
          metrics = concat(
            [for i, pool in local.instance_pools : ["AWS/SageMaker", "TotalProcessingTime", "EndpointName", local.endpoint_name, "VariantName", local.variant_name, "InstanceType", pool.instance_type, { stat = "Average", id = "proc_${i}", visible = false, period = 60 }]],
            [for i, pool in local.instance_pools : [{ expression = "proc_${i}/1000", label = pool.instance_type }]],
          )
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 22
        width  = 12
        height = 6
        properties = {
          title  = "Lambda errors"
          region = var.region
          view   = "timeSeries"
          period = 60
          metrics = [
            for name in local.dashboard_lambdas :
            ["AWS/Lambda", "Errors", "FunctionName", name, { stat = "Sum", label = name, period = 60 }]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 22
        width  = 12
        height = 6
        properties = {
          title  = "Lambda slowest run (seconds)"
          region = var.region
          view   = "timeSeries"
          period = 60
          metrics = concat(
            [for i, name in local.dashboard_lambdas : ["AWS/Lambda", "Duration", "FunctionName", name, { stat = "Maximum", id = "dur_${i}", visible = false, period = 60 }]],
            [for i, name in local.dashboard_lambdas : [{ expression = "dur_${i}/1000", label = name }]],
          )
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 28
        width  = 8
        height = 6
        properties = {
          title  = "Aurora capacity (ACU)"
          region = var.region
          view   = "timeSeries"
          period = 60
          metrics = [
            ["AWS/RDS", "ServerlessDatabaseCapacity", "DBClusterIdentifier", aws_rds_cluster.db.cluster_identifier, { stat = "Maximum", label = "Capacity (ACU)", period = 60 }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 8
        y      = 28
        width  = 16
        height = 6
        properties = {
          title  = "GPU guard stops and Cognito delete failures"
          region = var.region
          view   = "timeSeries"
          period = 60
          metrics = [
            ["NeuroLens", "GpuGuardStopped", { stat = "Sum", label = "GPU guard stops", period = 60 }],
            ["NeuroLens", "CognitoDeleteFailed", { stat = "Sum", label = "Cognito delete failures", period = 60 }],
          ]
        }
      },
    ]
  })
}
