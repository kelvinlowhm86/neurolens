# ─── GPU worker Launch Template and Auto Scaling group (M2a §4b) ───────────
# Created only once worker_ami_id is set (after infra/build_ami.sh has made the image). The group
# stands at zero with no scaling policy: infra/start_work.sh --worker starts one worker by hand.

locals {
  workers_enabled = var.worker_ami_id != ""
}

resource "aws_launch_template" "worker" {
  count = local.workers_enabled ? 1 : 0

  name          = "neurolens-worker"
  image_id      = var.worker_ami_id
  instance_type = var.worker_instance_types[0]

  iam_instance_profile {
    name = aws_iam_instance_profile.worker.name
  }

  vpc_security_group_ids = [aws_security_group.no_inbound.id]

  # The image's software fills 66 GB of its 75 GB root disk. 100 GB leaves room for videos and logs
  # (and, on a CPU rehearsal with no instance-store disk, the ~18 GB of weights). Billed only while
  # a worker exists.
  block_device_mappings {
    device_name = "/dev/sda1"
    ebs {
      volume_size           = 100
      volume_type           = "gp3"
      delete_on_termination = true
    }
  }

  # On-demand, not Spot: over the 90 days to 2026-10-03 Spot g6e averaged only 1-9% cheaper and was
  # repeatedly sold out in all four zones (docs/evidence/). No instance_market_options = on-demand.

  metadata_options {
    http_tokens = "required"
  }

  user_data = base64encode(templatefile("${path.module}/worker_userdata.sh.tftpl", {
    region         = "us-east-1"
    bucket         = aws_s3_bucket.main.bucket
    queue_url      = aws_sqs_queue.jobs.url
    fake_inference = var.worker_fake_inference
  }))

  tag_specifications {
    resource_type = "instance"
    tags          = { Project = "neurolens", Milestone = "M2a", Name = "neurolens-worker", Role = "worker" }
  }

  tag_specifications {
    resource_type = "volume"
    tags          = { Project = "neurolens", Milestone = "M2a", Name = "neurolens-worker" }
  }
}

resource "aws_autoscaling_group" "workers" {
  count = local.workers_enabled ? 1 : 0

  name                = local.worker_asg
  vpc_zone_identifier = aws_subnet.private[*].id
  min_size            = 0
  max_size            = 0
  desired_capacity    = 0

  # Tries the types in order: g6e.xlarge, then g6e.2xlarge when no xlarge is free in any zone.
  mixed_instances_policy {
    instances_distribution {
      on_demand_allocation_strategy            = "prioritized"
      on_demand_base_capacity                  = 0
      on_demand_percentage_above_base_capacity = 100
    }

    launch_template {
      launch_template_specification {
        launch_template_id = aws_launch_template.worker[0].id
        version            = aws_launch_template.worker[0].latest_version
      }

      dynamic "override" {
        for_each = var.worker_instance_types
        content {
          instance_type = override.value
        }
      }
    }
  }

  # Free; the 3-hour alarm reads GroupInServiceInstances.
  metrics_granularity = "1Minute"
  enabled_metrics     = ["GroupInServiceInstances", "GroupDesiredCapacity", "GroupPendingInstances"]

  tag {
    key                 = "Project"
    value               = "neurolens"
    propagate_at_launch = true
  }

  lifecycle {
    # start_work.sh / stop_work.sh change these; Terraform must not fight them.
    ignore_changes = [desired_capacity, min_size, max_size]
  }
}
