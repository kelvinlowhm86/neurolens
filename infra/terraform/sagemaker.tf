# ─── SageMaker asynchronous GPU endpoint (M3e §6a, §9) ────────────────────
#
# SageMaker starts a GPU when a request is waiting and stops it after 7 idle minutes. The
# container does the bookkeeping (claim, charge, refund); SageMaker only runs and stops machines.
# The endpoint configuration alone uses the awscc provider because instance pools cannot be
# expressed by the aws provider.

locals {
  endpoint_name = "neurolens"
  variant_name  = "main"
  image_ready   = var.worker_image != ""

  # The digest is stored as sha256:<hex>; accept either form so a copy from `docker inspect`
  # works without editing. "none" only fills the name while no image has been built yet.
  image_digest        = var.worker_image == "" ? "sha256:none" : (startswith(var.worker_image, "sha256:") ? var.worker_image : "sha256:${var.worker_image}")
  image_digest_prefix = var.worker_image == "" ? "none" : substr(replace(local.image_digest, "sha256:", ""), 0, 12)
  worker_image_uri    = "${aws_ecr_repository.worker.repository_url}@${local.image_digest}"

  # Fastest per dollar first; each fits the model's ~20 GB GPU peak with at least 32 GB of RAM.
  # SageMaker tries the next pool when one has no capacity (28 Oct: g6e and g6 were both out),
  # waiting up to 600 s per variant. Each type needs its own SageMaker "for endpoint usage" quota.
  # There is no CPU rehearsal: SageMaker refuses a CUDA image on a CPU type.
  instance_pools = [
    { instance_type = "ml.g6e.xlarge", priority = 1 },
    { instance_type = "ml.g6.2xlarge", priority = 2 },
    { instance_type = "ml.g5.2xlarge", priority = 3 },
    { instance_type = "ml.g6e.2xlarge", priority = 4 },
  ]

  # Endpoint configurations cannot be changed in place, so the name changes with all of their
  # content. A new image (or a fake/real switch) creates a new configuration before the old one
  # is removed, and the endpoint swaps to it.
  endpoint_config_hash = substr(sha256(jsonencode({
    model     = local.model_name
    pools     = local.instance_pools
    bucket    = aws_s3_bucket.main.bucket
    topic     = aws_sns_topic.gpu_errors.arn
    startup   = 1800
    download  = 1800
    provision = 600
  })), 0, 8)
  endpoint_config_name = "neurolens-${local.endpoint_config_hash}"

  endpoint_arn = "arn:aws:sagemaker:${var.region}:${local.account_id}:endpoint/${local.endpoint_name}"
  model_name   = "neurolens-${local.image_digest_prefix}"
}

# ─── The image repository and its build ───────────────────────────────────

resource "aws_ecr_repository" "worker" {
  name                 = "neurolens-worker"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Milestone = "M3e" }
}

# The image the endpoint runs carries the tag `live` and must never expire (SageMaker pulls it
# again at every start from zero). ECR never expires an image matched by a higher-priority rule,
# so the `live` rule above protects it from the keep-last-3 rule below. Of the rest, the last
# three commits are enough to roll back without a rebuild.
resource "aws_ecr_lifecycle_policy" "worker" {
  repository = aws_ecr_repository.worker.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Never expire the live image"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["live"]
          countType     = "imageCountMoreThan"
          countNumber   = 1000
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep the last 3 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 3
        }
        action = { type = "expire" }
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "codebuild" {
  name              = "/aws/codebuild/neurolens-worker-image"
  retention_in_days = 30
  tags              = { Milestone = "M3e" }
}

# Linux x86-64, Docker in privileged mode, one hour. The source is the repository at HEAD,
# uploaded by infra/build_image.sh; the buildspec is inside that zip.
resource "aws_codebuild_project" "worker_image" {
  name          = "neurolens-worker-image"
  description   = "Builds the NeuroLens SageMaker container image and pushes it to ECR."
  service_role  = aws_iam_role.codebuild.arn
  build_timeout = 60

  source {
    type      = "S3"
    location  = "${aws_s3_bucket.main.bucket}/build/source.zip"
    buildspec = "infra/worker_image/buildspec.yml"
  }

  environment {
    type            = "LINUX_CONTAINER"
    compute_type    = "BUILD_GENERAL1_MEDIUM"
    image           = "aws/codebuild/standard:7.0"
    privileged_mode = true

    environment_variable {
      name  = "REPOSITORY_NAME"
      value = aws_ecr_repository.worker.name
    }

    environment_variable {
      name  = "AWS_ACCOUNT_ID"
      value = local.account_id
    }
  }

  artifacts {
    type = "NO_ARTIFACTS"
  }

  logs_config {
    cloudwatch_logs {
      group_name  = aws_cloudwatch_log_group.codebuild.name
      stream_name = "worker-image"
    }
  }

  tags = { Milestone = "M3e" }
}

# ─── The model, endpoint configuration and endpoint ───────────────────────

resource "aws_sagemaker_model" "main" {
  count = local.image_ready ? 1 : 0

  name               = local.model_name
  execution_role_arn = aws_iam_role.sagemaker.arn

  primary_container {
    image = local.worker_image_uri

    # SageMaker copies this uncompressed prefix into /opt/ml/model at instance start
    # (model_data_url would mean one .tar.gz object).
    model_data_source {
      s3_data_source {
        s3_uri           = "s3://${aws_s3_bucket.main.bucket}/model-data/"
        s3_data_type     = "S3Prefix"
        compression_type = "None"
      }
    }

    environment = {
      NEUROLENS_AWS_REGION     = var.region
      NEUROLENS_S3_BUCKET      = aws_s3_bucket.main.bucket
      NEUROLENS_DB_CLUSTER_ARN = aws_rds_cluster.db.arn
      NEUROLENS_DB_SECRET_ARN  = local.db_secret_arn
      NEUROLENS_DB_NAME        = local.db_name
      NEUROLENS_DEPLOYED       = "1"
      NEUROLENS_ROOT           = "/opt/neurolens"
      HF_HUB_OFFLINE           = "1"
      UV_OFFLINE               = "1"
    }
  }

  tags = { Milestone = "M3e" }
}

# The endpoint configuration is the one resource Cloud Control manages (instance pools, §6a).
resource "awscc_sagemaker_endpoint_config" "main" {
  count = local.image_ready ? 1 : 0

  endpoint_config_name = local.endpoint_config_name

  production_variants = [{
    variant_name                                      = local.variant_name
    model_name                                        = aws_sagemaker_model.main[0].name
    initial_instance_count                            = 1
    instance_pools                                    = local.instance_pools
    variant_instance_provision_timeout_in_seconds     = 600
    container_startup_health_check_timeout_in_seconds = 1800
    model_data_download_timeout_in_seconds            = 1800
  }]

  async_inference_config = {
    output_config = {
      s3_output_path  = "s3://${aws_s3_bucket.main.bucket}/sagemaker-output/ok/"
      s3_failure_path = "s3://${aws_s3_bucket.main.bucket}/sagemaker-output/failed/"
      notification_config = {
        error_topic = aws_sns_topic.gpu_errors.arn
      }
    }
    client_config = {
      max_concurrent_invocations_per_instance = 1
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

# The container's log group: its lines carry user ids and object keys, so they are kept 14 days.

resource "aws_cloudwatch_log_group" "sagemaker_endpoint" {
  name              = "/aws/sagemaker/Endpoints/neurolens"
  retention_in_days = var.log_retention_days
  tags              = { Milestone = "M3e" }
}

resource "aws_sagemaker_endpoint" "main" {
  count = local.image_ready ? 1 : 0

  name                 = local.endpoint_name
  endpoint_config_name = awscc_sagemaker_endpoint_config.main[0].endpoint_config_name

  depends_on = [aws_cloudwatch_log_group.sagemaker_endpoint]

  tags = { Milestone = "M3e" }

  # An update SageMaker rolls back (for example no GPU capacity) ends InService on the old
  # configuration, and the provider reports success. Without this check Terraform would then
  # delete the old configuration and model the endpoint still uses (30 Oct), leaving it unable
  # to start or update. Failing here keeps them.
  lifecycle {
    postcondition {
      condition     = self.endpoint_config_name == awscc_sagemaker_endpoint_config.main[0].endpoint_config_name
      error_message = "The endpoint did not switch to the new configuration (SageMaker rolled the update back; see FailureReason in describe-endpoint). The old configuration and model are kept: run the apply again later."
    }
  }
}

# The endpoint resource can return while SageMaker is still updating it, and the scaling target is
# refused until it is back in service (31 Oct). Wait for that whenever the configuration changes,
# then fail unless the endpoint runs the new configuration: an update SageMaker rolls back also
# ends InService, and the old configuration and model must then be kept (see the postcondition).
# Referencing the configuration directly makes Terraform delete the old one only after this passes.
resource "terraform_data" "endpoint_in_service" {
  count = local.image_ready ? 1 : 0

  triggers_replace = awscc_sagemaker_endpoint_config.main[0].endpoint_config_name

  depends_on = [aws_sagemaker_endpoint.main]

  provisioner "local-exec" {
    command = <<-EOT
      set -eu
      aws sagemaker wait endpoint-in-service \
        --endpoint-name ${aws_sagemaker_endpoint.main[0].name} --region ${var.region}
      running=$(aws sagemaker describe-endpoint \
        --endpoint-name ${aws_sagemaker_endpoint.main[0].name} --region ${var.region} \
        --query EndpointConfigName --output text)
      if [ "$running" != "${awscc_sagemaker_endpoint_config.main[0].endpoint_config_name}" ]; then
        echo "The endpoint runs $running, not the new configuration (SageMaker rolled the update back; see FailureReason in describe-endpoint). The old configuration and model are kept: run the apply again later." >&2
        exit 1
      fi
    EOT
  }
}

# The lifecycle policy's first rule protects the `live` tag. Move it to the deployed digest
# whenever worker_image changes; `--image-tag` with a manifest moves an existing tag.
resource "terraform_data" "live_image" {
  count = local.image_ready ? 1 : 0

  triggers_replace = local.image_digest

  depends_on = [terraform_data.endpoint_in_service]

  provisioner "local-exec" {
    command = <<-EOT
      set -eu
      manifest=$(aws ecr batch-get-image \
        --repository-name ${aws_ecr_repository.worker.name} \
        --image-ids imageDigest=${local.image_digest} \
        --region ${var.region} \
        --query 'images[0].imageManifest' --output text)
      if ! aws ecr put-image \
        --repository-name ${aws_ecr_repository.worker.name} \
        --image-tag live \
        --image-manifest "$manifest" \
        --region ${var.region}; then
        # A re-run after a partial apply finds the same digest already tagged: ECR reports
        # ImageAlreadyExistsException. Any other failure stays fatal.
        current=$(aws ecr batch-get-image \
          --repository-name ${aws_ecr_repository.worker.name} \
          --image-ids imageTag=live \
          --region ${var.region} \
          --query 'images[0].imageDigest' --output text 2>/dev/null || true)
        test "$current" = "${local.image_digest}"
      fi
    EOT
  }
}

# The endpoint's error notifications (no success topic: the container settles a success itself).
resource "aws_sns_topic" "gpu_errors" {
  name = "neurolens-gpu-errors"
  tags = { Milestone = "M3e" }
}

# ─── Scaling: a GPU on demand, back to 0 after 7 idle minutes (M3e §6a) ───

resource "aws_appautoscaling_target" "endpoint" {
  count = local.image_ready ? 1 : 0

  service_namespace  = "sagemaker"
  resource_id        = "endpoint/${local.endpoint_name}/variant/${local.variant_name}"
  scalable_dimension = "sagemaker:variant:DesiredInstanceCount"
  min_capacity       = 0
  max_capacity       = var.max_instances

  depends_on = [terraform_data.endpoint_in_service]
}

# One waiting request with no capacity adds one instance.
resource "aws_appautoscaling_policy" "scale_out" {
  count = local.image_ready ? 1 : 0

  name               = "neurolens-gpu-scale-out"
  policy_type        = "StepScaling"
  service_namespace  = "sagemaker"
  resource_id        = aws_appautoscaling_target.endpoint[0].resource_id
  scalable_dimension = aws_appautoscaling_target.endpoint[0].scalable_dimension

  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    metric_aggregation_type = "Maximum"

    step_adjustment {
      scaling_adjustment          = 1
      metric_interval_lower_bound = 0
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "scale_out" {
  count = local.image_ready ? 1 : 0

  alarm_name        = "neurolens-gpu-scale-out"
  alarm_description = "A NeuroLens request is waiting with no GPU capacity: start one."

  namespace   = "AWS/SageMaker"
  metric_name = "HasBacklogWithoutCapacity"
  # The async backlog metrics carry only EndpointName (AWS async metric docs); adding VariantName
  # would match no data at all.
  dimensions = {
    EndpointName = local.endpoint_name
  }
  statistic = "Maximum"

  period              = 60
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_appautoscaling_policy.scale_out[0].arn]
  tags          = { Milestone = "M3e" }
}

# The backlog counts requests in progress too, so a running job holds the GPU. Missing data
# counts as breaching: AWS may stop publishing the backlog when idle, and an alarm without data
# must not keep a GPU on.
resource "aws_appautoscaling_policy" "scale_in" {
  count = local.image_ready ? 1 : 0

  name               = "neurolens-gpu-scale-in"
  policy_type        = "StepScaling"
  service_namespace  = "sagemaker"
  resource_id        = aws_appautoscaling_target.endpoint[0].resource_id
  scalable_dimension = aws_appautoscaling_target.endpoint[0].scalable_dimension

  step_scaling_policy_configuration {
    adjustment_type         = "ExactCapacity"
    metric_aggregation_type = "Maximum"

    # The alarm breaches at or below its threshold, so the step covers the interval below it.
    step_adjustment {
      scaling_adjustment          = 0
      metric_interval_upper_bound = 0
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "scale_in" {
  count = local.image_ready ? 1 : 0

  alarm_name        = "neurolens-gpu-scale-in"
  alarm_description = "The NeuroLens endpoint has been idle for 7 minutes: stop its GPU."

  namespace   = "AWS/SageMaker"
  metric_name = "ApproximateBacklogSize"
  # The async backlog metrics carry only EndpointName (AWS async metric docs); adding VariantName
  # would match no data at all.
  dimensions = {
    EndpointName = local.endpoint_name
  }
  statistic = "Maximum"

  period              = 60
  evaluation_periods  = 7
  datapoints_to_alarm = 7
  comparison_operator = "LessThanOrEqualToThreshold"
  threshold           = 0
  treat_missing_data  = "breaching"

  alarm_actions = [aws_appautoscaling_policy.scale_in[0].arn]
  tags          = { Milestone = "M3e" }
}
