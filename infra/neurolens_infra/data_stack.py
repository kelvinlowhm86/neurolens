"""
Stateful pieces that survive `make teardown`: S3 bucket, the job queues (with the S3 → SQS upload
notification), Aurora Serverless v2, ECR repositories and SSM parameters.
"""

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_rds as rds
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_notifications as s3n
from aws_cdk import aws_secretsmanager as sm
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_ssm as ssm
from constructs import Construct

APP_SECRET_NAME = "neurolens/app"          # created by scripts/put_secrets.py, never by CDK
IMAGE_TAG_PARAM = "/neurolens/worker/image_tag"
LAST_JOB_PARAM = "/neurolens/last_job_created_at"


class DataStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, vpc: ec2.IVpc, db_sg: ec2.ISecurityGroup,
                 aurora_min_acu: float, aurora_max_acu: float, **kw):
        super().__init__(scope, cid, **kw)

        # ─── S3 ──────────────────────────────────────────────────────────────
        self.bucket = s3.Bucket(
            self, "Media",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            # Browser uploads use a presigned POST whose policy fixes key, size and type, so "*" origins
            # only allow what the web tier already signed.
            cors=[s3.CorsRule(allowed_methods=[s3.HttpMethods.POST, s3.HttpMethods.GET],
                              allowed_origins=["*"], allowed_headers=["*"], max_age=3000)],
            lifecycle_rules=[
                s3.LifecycleRule(id="uploads-48h", prefix="uploads/", expiration=Duration.days(2)),
                s3.LifecycleRule(id="results-30d", prefix="results/", expiration=Duration.days(30)),
                s3.LifecycleRule(id="abort-mpu", abort_incomplete_multipart_upload_after=Duration.days(1)),
            ],
        )

        # ─── SQS: jobs + DLQ (2 genuine failures), fed by S3 ObjectCreated ───
        self.dlq = sqs.Queue(self, "JobsDlq", queue_name="neurolens-jobs-dlq",
                             retention_period=Duration.days(14), enforce_ssl=True)
        self.queue = sqs.Queue(
            self, "Jobs", queue_name="neurolens-jobs",
            visibility_timeout=Duration.seconds(300),
            retention_period=Duration.days(1),
            receive_message_wait_time=Duration.seconds(20),
            dead_letter_queue=sqs.DeadLetterQueue(queue=self.dlq, max_receive_count=2),
            enforce_ssl=True,
        )
        self.bucket.add_event_notification(s3.EventType.OBJECT_CREATED, s3n.SqsDestination(self.queue),
                                           s3.NotificationKeyFilter(prefix="uploads/"))

        # ─── Aurora PostgreSQL Serverless v2 (auto-pauses at 0 ACU) ─────────
        self.db = rds.DatabaseCluster(
            self, "Db",
            engine=rds.DatabaseClusterEngine.aurora_postgres(version=rds.AuroraPostgresEngineVersion.VER_16_4),
            credentials=rds.Credentials.from_generated_secret("neurolens", secret_name="neurolens/db"),
            default_database_name="neurolens",
            writer=rds.ClusterInstance.serverless_v2("writer"),
            serverless_v2_min_capacity=aurora_min_acu,
            serverless_v2_max_capacity=aurora_max_acu,
            enable_data_api=True,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[db_sg],
            storage_encrypted=True,
            removal_policy=RemovalPolicy.SNAPSHOT,
        )
        self.db_secret = self.db.secret

        self.app_secret = sm.Secret.from_secret_name_v2(self, "AppSecret", APP_SECRET_NAME)

        # ─── Images + parameters ─────────────────────────────────────────────
        keep_10 = [ecr.LifecycleRule(max_image_count=10)]
        self.web_repo = ecr.Repository(self, "WebRepo", repository_name="neurolens-web", lifecycle_rules=keep_10,
                                       removal_policy=RemovalPolicy.RETAIN)
        self.worker_repo = ecr.Repository(self, "WorkerRepo", repository_name="neurolens-worker",
                                          lifecycle_rules=keep_10, removal_policy=RemovalPolicy.RETAIN)

        self.image_tag_param = ssm.StringParameter(self, "WorkerImageTag", parameter_name=IMAGE_TAG_PARAM,
                                                   string_value="latest",
                                                   description="Worker image tag pulled at boot (make release-worker)")
        self.last_job_param = ssm.StringParameter(self, "LastJobCreated", parameter_name=LAST_JOB_PARAM,
                                                  string_value="never",
                                                  description="Stamped by the web tier; lets the reaper skip an idle DB")

        CfnOutput(self, "BucketName", value=self.bucket.bucket_name)
        CfnOutput(self, "JobsQueueUrl", value=self.queue.queue_url)
        CfnOutput(self, "WebRepoUri", value=self.web_repo.repository_uri)
        CfnOutput(self, "WorkerRepoUri", value=self.worker_repo.repository_uri)
