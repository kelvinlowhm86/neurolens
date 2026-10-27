"""
Request path and GPU pool:

  CloudFront (TLS, *.cloudfront.net) → ALB (secret-header gate) → ECS Fargate web tasks
  EC2 Auto Scaling Group of Spot g6e.2xlarge workers (min 0), booted from the Packer AMI

Scaling: the web tier raises desired capacity the moment an upload starts (pre-warm); a step-scaling
policy on queue depth is the backup. Scale-in is never done by the ASG — idle workers terminate
themselves, and busy ones hold scale-in protection.
"""

from aws_cdk import CfnOutput, Duration, SecretValue, Stack
from aws_cdk import aws_autoscaling as autoscaling
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from constructs import Construct

from .data_stack import APP_SECRET_NAME, DataStack
from .network_stack import NetworkStack

ASG_NAME = "neurolens-workers"
ORIGIN_HEADER = "X-Origin-Verify"


class ComputeStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, net: NetworkStack, data: DataStack, ctx: dict, **kw):
        super().__init__(scope, cid, **kw)
        region = Stack.of(self).region
        origin_secret = SecretValue.secrets_manager(APP_SECRET_NAME, json_field="origin_verify_secret")

        # ─── ALB + CloudFront ────────────────────────────────────────────────
        alb = elbv2.ApplicationLoadBalancer(self, "Alb", vpc=net.vpc, internet_facing=True,
                                            security_group=net.alb_sg,
                                            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC))
        listener = alb.add_listener("Http", port=80, open=False, default_action=elbv2.ListenerAction.fixed_response(
            403, content_type="text/plain", message_body="Forbidden"))

        origin = origins.LoadBalancerV2Origin(
            alb, protocol_policy=cloudfront.OriginProtocolPolicy.HTTP_ONLY,
            custom_headers={ORIGIN_HEADER: origin_secret.unsafe_unwrap()}, read_timeout=Duration.seconds(60))
        dist = cloudfront.Distribution(
            self, "Cdn",
            comment="NeuroLens",
            price_class=cloudfront.PriceClass.PRICE_CLASS_200,      # includes Asia
            default_behavior=cloudfront.BehaviorOptions(
                origin=origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER,
            ),
            additional_behaviors={"/static/*": cloudfront.BehaviorOptions(
                origin=origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            )},
        )
        public_url = f"https://{dist.distribution_domain_name}"

        # ─── Web tier: ECS Fargate ───────────────────────────────────────────
        cluster = ecs.Cluster(self, "Cluster", vpc=net.vpc, container_insights_v2=ecs.ContainerInsights.DISABLED)
        task = ecs.FargateTaskDefinition(self, "WebTask", cpu=256, memory_limit_mib=512,
                                         runtime_platform=ecs.RuntimePlatform(
                                             cpu_architecture=ecs.CpuArchitecture.X86_64,
                                             operating_system_family=ecs.OperatingSystemFamily.LINUX))
        task.add_container(
            "web",
            image=ecs.ContainerImage.from_ecr_repository(data.web_repo, ctx["web_image_tag"]),
            port_mappings=[ecs.PortMapping(container_port=8000)],
            logging=ecs.LogDrivers.aws_logs(stream_prefix="web", log_retention=logs.RetentionDays.TWO_WEEKS),
            environment={
                "APP_ENV": "aws",
                "AWS_REGION": region,
                "PUBLIC_BASE_URL": public_url,
                "DB_SECRET_ARN": data.db_secret.secret_arn,
                "APP_SECRET_ARN": data.app_secret.secret_arn,
                "S3_BUCKET": data.bucket.bucket_name,
                "ASG_NAME": ASG_NAME,
                "MAX_WORKERS": str(ctx["max_workers"]),
                "PREWARM_ENABLED": str(ctx["prewarm_enabled"]).lower(),
                "SAMPLES_SOURCE": "s3",
                "LAST_JOB_PARAM": data.last_job_param.parameter_name,
            },
        )
        data.db_secret.grant_read(task.task_role)
        data.app_secret.grant_read(task.task_role)
        data.bucket.grant_put(task.task_role, "uploads/*")         # presigned POST is signed with these creds
        data.bucket.grant_read(task.task_role)                     # results, inputs (video playback), samples
        data.last_job_param.grant_write(task.task_role)
        task.task_role.add_to_policy(iam.PolicyStatement(
            actions=["autoscaling:DescribeAutoScalingGroups"], resources=["*"]))
        task.task_role.add_to_policy(iam.PolicyStatement(
            actions=["autoscaling:SetDesiredCapacity"],
            resources=[f"arn:aws:autoscaling:{region}:{self.account}:autoScalingGroup:*:autoScalingGroupName/{ASG_NAME}"]))

        service = ecs.FargateService(
            self, "Web", cluster=cluster, task_definition=task, desired_count=ctx["web_desired"],
            assign_public_ip=True, security_groups=[net.web_sg],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            min_healthy_percent=100,
        )
        tg = elbv2.ApplicationTargetGroup(
            self, "WebTg", vpc=net.vpc, port=8000, protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[service], deregistration_delay=Duration.seconds(15),
            health_check=elbv2.HealthCheck(path="/healthz", healthy_http_codes="200", interval=Duration.seconds(15)),
        )
        listener.add_action("FromCloudFront", priority=10, action=elbv2.ListenerAction.forward([tg]),
                            conditions=[elbv2.ListenerCondition.http_header(ORIGIN_HEADER,
                                                                            [origin_secret.unsafe_unwrap()])])
        scaling = service.auto_scale_task_count(min_capacity=ctx["web_desired"], max_capacity=3)
        scaling.scale_on_cpu_utilization("Cpu", target_utilization_percent=60)

        # ─── GPU workers: Spot ASG from the Packer AMI ───────────────────────
        worker_role = iam.Role(self, "WorkerRole", assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
                               managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name(
                                   "AmazonSSMManagedInstanceCore")])         # SSM Session Manager, no SSH
        data.queue.grant_consume_messages(worker_role)
        data.queue.grant_send_messages(worker_role)                 # Spot requeue
        data.bucket.grant_read(worker_role, "uploads/*")
        data.bucket.grant_put(worker_role, "results/*")
        data.db_secret.grant_read(worker_role)
        data.worker_repo.grant_pull(worker_role)
        data.image_tag_param.grant_read(worker_role)
        worker_role.add_to_policy(iam.PolicyStatement(
            actions=["autoscaling:SetInstanceProtection", "autoscaling:TerminateInstanceInAutoScalingGroup"],
            resources=[f"arn:aws:autoscaling:{region}:{self.account}:autoScalingGroup:*:autoScalingGroupName/{ASG_NAME}"]))
        # AI report: Claude on Amazon Bedrock with this role (no API key on AWS).
        worker_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], resources=["*"]))
        worker_role.add_to_policy(iam.PolicyStatement(
            actions=["ec2:DescribeInstances", "cloudwatch:PutMetricData", "logs:CreateLogStream",
                     "logs:PutLogEvents", "logs:CreateLogGroup"], resources=["*"]))

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -euo pipefail",
            "mkdir -p /etc/neurolens",
            "cat > /etc/neurolens/worker.env <<'EOF'",
            "APP_ENV=aws",
            f"AWS_REGION={region}",
            f"AWS_DEFAULT_REGION={region}",
            f"SQS_QUEUE_URL={data.queue.queue_url}",
            f"S3_BUCKET={data.bucket.bucket_name}",
            f"DB_SECRET_ARN={data.db_secret.secret_arn}",
            f"ASG_NAME={ASG_NAME}",
            f"SILENT_MODE={ctx['silent_mode']}",
            f"IDLE_SHUTDOWN_SECONDS={ctx['idle_shutdown_seconds']}",
            f"REPORT_LLM={ctx['report_llm']}",
            f"BEDROCK_MODEL={ctx['bedrock_model']}",
            f"BEDROCK_REGION={ctx['bedrock_region'] or region}",
            "EOF",
            "cat > /etc/neurolens/boot.env <<'EOF'",
            f"ECR_REPO={data.worker_repo.repository_uri}",
            f"IMAGE_TAG_PARAM={data.image_tag_param.parameter_name}",
            f"AWS_REGION={region}",
            "EOF",
            # Scratch on the instance-store NVMe if the instance has one (g6e.2xlarge: 450 GB).
            "/usr/local/bin/neurolens-scratch.sh || true",
            "systemctl restart neurolens-worker.service",
        )

        lt = ec2.LaunchTemplate(
            self, "WorkerLt",
            machine_image=ec2.MachineImage.generic_linux({region: ctx["worker_ami_id"]}),
            instance_type=ec2.InstanceType(ctx["worker_instance_types"][0]),
            role=worker_role,
            security_group=net.worker_sg,
            user_data=user_data,
            require_imdsv2=True,
            http_put_response_hop_limit=2,                   # the container must reach IMDS (Spot notices)
            associate_public_ip_address=True,
            block_devices=[ec2.BlockDevice(device_name="/dev/sda1", volume=ec2.BlockDeviceVolume.ebs(
                150, volume_type=ec2.EbsDeviceVolumeType.GP3, encrypted=True, delete_on_termination=True))],
        )
        spot = ctx["worker_spot"]
        asg = autoscaling.AutoScalingGroup(
            self, "Workers",
            auto_scaling_group_name=ASG_NAME,
            vpc=net.vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            min_capacity=0,
            max_capacity=ctx["max_workers"],
            capacity_rebalance=spot,
            group_metrics=[autoscaling.GroupMetrics.all()],
            mixed_instances_policy=autoscaling.MixedInstancesPolicy(
                launch_template=lt,
                launch_template_overrides=[autoscaling.LaunchTemplateOverrides(instance_type=ec2.InstanceType(t))
                                           for t in ctx["worker_instance_types"]],
                instances_distribution=autoscaling.InstancesDistribution(
                    on_demand_base_capacity=0,
                    on_demand_percentage_above_base_capacity=0 if spot else 100,
                    spot_allocation_strategy=autoscaling.SpotAllocationStrategy.PRICE_CAPACITY_OPTIMIZED,
                ),
            ),
        )
        # Backup scale-out on queue depth (pre-warm from the web tier is the fast path). Never scales in.
        asg.scale_on_metric(
            "QueueDepth",
            metric=data.queue.metric_approximate_number_of_messages_visible(period=Duration.minutes(1),
                                                                             statistic="Maximum"),
            scaling_steps=[
                autoscaling.ScalingInterval(upper=0, change=0),
                autoscaling.ScalingInterval(lower=1, change=+1),
                autoscaling.ScalingInterval(lower=3, change=+2),
            ],
            adjustment_type=autoscaling.AdjustmentType.CHANGE_IN_CAPACITY,
            evaluation_periods=1,
        )

        self.alb = alb
        self.dist = dist
        self.service = service
        self.asg = asg

        CfnOutput(self, "PublicUrl", value=public_url)
        CfnOutput(self, "OAuthRedirectUri", value=f"{public_url}/auth/callback")
        CfnOutput(self, "StripeWebhookUrl", value=f"{public_url}/api/billing/webhook")
        CfnOutput(self, "WorkerAsg", value=ASG_NAME)
