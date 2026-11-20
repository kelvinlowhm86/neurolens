# ─── Aurora PostgreSQL through the Data API ──────────────────────
#
# Users, credit balances and job state. Serverless v2 from 0 ACU: it pauses after 5 idle minutes
# and wakes in about 15 s on the next call, so it costs only while a session or a job uses it.
# Reached only through the RDS Data API (HTTPS, IAM-checked): no workload opens a connection to
# port 5432, so its security group has no rules at all and nothing needs to sit in the VPC.

locals {
  db_name = "neurolens"
  # 16.3 or later is needed to scale to zero. 16.15 is offered in us-east-1 with ServerlessV2
  # MinCapacity 0 (`aws rds describe-db-engine-versions`); minor upgrades are off.
  db_engine_version = "16.15"
}

resource "aws_db_subnet_group" "db" {
  name       = "neurolens-db"
  subnet_ids = aws_subnet.private[*].id
}

# No ingress and no egress rule: Terraform removes AWS's default allow-all egress rule.
resource "aws_security_group" "db" {
  name        = "neurolens-db"
  description = "NeuroLens Aurora: no rules (reached only through the RDS Data API)"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "neurolens-db" }
}

resource "aws_rds_cluster" "db" {
  cluster_identifier = "neurolens-db"
  engine             = "aurora-postgresql"
  engine_mode        = "provisioned"
  engine_version     = local.db_engine_version
  database_name      = local.db_name
  master_username    = "neurolens_admin"
  # RDS creates the password and keeps it in Secrets Manager (rotated every 7 days); the Data API
  # reads that secret on each call, so it is never in Terraform state, config or code.
  manage_master_user_password = true
  enable_http_endpoint        = true

  db_subnet_group_name   = aws_db_subnet_group.db.name
  vpc_security_group_ids = [aws_security_group.db.id]
  storage_encrypted      = true # the AWS-managed key: free

  serverlessv2_scaling_configuration {
    min_capacity             = 0
    max_capacity             = 1 # our load is a few small queries; each wake resumes at the maximum
    seconds_until_auto_pause = 300
  }

  # 1 day of automatic backups (free at this size). `terraform destroy` works without a manual step
  # And really deletes the data (teardown relies on this).
  backup_retention_period  = 1
  skip_final_snapshot      = true
  delete_automated_backups = true
  deletion_protection      = false
  apply_immediately        = true

}

resource "aws_rds_cluster_instance" "db" {
  identifier                 = "neurolens-db-1"
  cluster_identifier         = aws_rds_cluster.db.id
  instance_class             = "db.serverless"
  engine                     = aws_rds_cluster.db.engine
  engine_version             = aws_rds_cluster.db.engine_version
  publicly_accessible        = false
  auto_minor_version_upgrade = false # no surprise upgrade (and Terraform drift) mid-project
}

locals {
  db_secret_arn = aws_rds_cluster.db.master_user_secret[0].secret_arn
  # What the worker and both Lambdas may do with the database: the Data API on this cluster, and
  # read its password secret (which the Data API uses on their behalf).
  db_access_statements = [
    {
      Sid      = "DataApiOnTheNeurolensDatabase"
      Effect   = "Allow"
      Action   = ["rds-data:ExecuteStatement", "rds-data:BeginTransaction", "rds-data:CommitTransaction", "rds-data:RollbackTransaction"]
      Resource = aws_rds_cluster.db.arn
    },
    {
      Sid      = "ReadTheDatabaseSecret"
      Effect   = "Allow"
      Action   = "secretsmanager:GetSecretValue"
      Resource = local.db_secret_arn
    },
  ]
}

# Forgotten-database alarm: awake in more than 30% of the minutes of each of 6 hours in a
# row means something keeps it awake (an open tab, a retrying Lambda): about $2.90 a day at 1 ACU (estimate).
# The metric reads 0 while paused, so the hourly 70th percentile of its per-minute values is above 0
# exactly when it was awake for over 30% of the hour, whatever capacity it ran at. A reaper wake
# (about 11 minutes, measured) stays below that; a caller every 12 minutes (about 45%) is above it.
resource "aws_cloudwatch_metric_alarm" "db_awake_long" {
  alarm_name        = "neurolens-db-awake-6h"
  alarm_description = "NeuroLens Aurora has been awake for a large share (over 30%) of each of the last 6 hours (about $2.90 a day while awake). Run infra/stop_work.sh; if it stays awake, look for whatever keeps calling it (an open page, a Lambda retrying: see /aws/lambda/neurolens-*)."

  namespace          = "AWS/RDS"
  metric_name        = "ServerlessDatabaseCapacity"
  dimensions         = { DBClusterIdentifier = aws_rds_cluster.db.cluster_identifier }
  extended_statistic = "p70"

  period              = 3600
  evaluation_periods  = 6
  datapoints_to_alarm = 6
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}
