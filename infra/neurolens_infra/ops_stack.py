"""
Billing integrity + observability + experiments:

  * dlq_refund Lambda (SQS DLQ trigger) and sla_reaper Lambda (EventBridge every 5 min) — RDS Data API
  * CloudWatch dashboard and alarms → SNS email
  * AWS FIS template that sends a Spot interruption to one running worker (Experiment 2)
  * AWS Budgets monthly cost alert
"""

from pathlib import Path

from aws_cdk import Duration, Stack
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_fis as fis
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as sources
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from constructs import Construct

from .compute_stack import ASG_NAME, ComputeStack
from .data_stack import DataStack


class OpsStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, data: DataStack, compute: ComputeStack,
                 lambda_asset: Path, ctx: dict, **kw):
        super().__init__(scope, cid, **kw)

        alerts = sns.Topic(self, "Alerts", display_name="NeuroLens alerts")
        if ctx.get("alert_email"):
            alerts.add_subscription(subs.EmailSubscription(ctx["alert_email"]))

        # ─── Billing-integrity Lambdas (Data API: no VPC, no NAT) ────────────
        code = lambda_.Code.from_asset(str(lambda_asset))
        common_env = {"DB_CLUSTER_ARN": data.db.cluster_arn, "DB_SECRET_ARN": data.db_secret.secret_arn,
                      "DB_NAME": "neurolens"}

        def make(name: str, handler: str, env: dict) -> lambda_.Function:
            fn = lambda_.Function(
                self, name, runtime=lambda_.Runtime.PYTHON_3_12, handler=handler, code=code,
                timeout=Duration.seconds(60), memory_size=256, environment={**common_env, **env},
                log_retention=logs.RetentionDays.TWO_WEEKS,
            )
            data.db.grant_data_api_access(fn)
            return fn

        refund = make("DlqRefund", "dlq_refund.handler", {})
        refund.add_event_source(sources.SqsEventSource(data.dlq, batch_size=10, report_batch_item_failures=True))

        reaper = make("SlaReaper", "sla_reaper.handler", {
            "JOBS_QUEUE_URL": data.queue.queue_url,
            "LAST_JOB_PARAM": data.last_job_param.parameter_name,
        })
        data.queue.grant(reaper, "sqs:GetQueueAttributes")
        data.last_job_param.grant_read(reaper)
        events.Rule(self, "ReaperSchedule", schedule=events.Schedule.rate(Duration.minutes(5)),
                    targets=[targets.LambdaFunction(reaper)])

        # ─── Alarms ──────────────────────────────────────────────────────────
        dlq_alarm = cw.Alarm(self, "DlqNotEmpty", metric=data.dlq.metric_approximate_number_of_messages_visible(),
                             threshold=1, evaluation_periods=1,
                             alarm_description="A job failed twice (credits are refunded automatically)")
        dlq_alarm.add_alarm_action(cw_actions.SnsAction(alerts))

        in_service = cw.Metric(namespace="AWS/AutoScaling", metric_name="GroupInServiceInstances",
                               dimensions_map={"AutoScalingGroupName": ASG_NAME}, statistic="Minimum",
                               period=Duration.minutes(5))
        runaway = cw.Alarm(self, "GpuRunningLong", metric=in_service, threshold=1, evaluation_periods=24,
                           comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                           alarm_description="A GPU worker has been running for 2 hours — check it is not stuck")
        runaway.add_alarm_action(cw_actions.SnsAction(alerts))

        # ─── Dashboard (Experiments 1–2 live view) ───────────────────────────
        def nl(name: str, stat: str = "Average") -> cw.Metric:
            return cw.Metric(namespace="NeuroLens", metric_name=name, statistic=stat, period=Duration.minutes(1))

        dash = cw.Dashboard(self, "Dashboard", dashboard_name="NeuroLens")
        dash.add_widgets(
            cw.GraphWidget(title="Queue", left=[data.queue.metric_approximate_number_of_messages_visible(),
                                                 data.queue.metric_approximate_number_of_messages_not_visible(),
                                                 data.dlq.metric_approximate_number_of_messages_visible()]),
            cw.GraphWidget(title="GPU workers", left=[
                cw.Metric(namespace="AWS/AutoScaling", metric_name=m, dimensions_map={"AutoScalingGroupName": ASG_NAME},
                          statistic="Maximum", period=Duration.minutes(1))
                for m in ("GroupDesiredCapacity", "GroupPendingInstances", "GroupInServiceInstances")]),
        )
        dash.add_widgets(
            cw.GraphWidget(title="Per-job GPU time (s)", left=[nl("PassFullSeconds"), nl("PassSilentSeconds")],
                           right=[nl("QueueWaitSeconds", "p95")]),
            cw.GraphWidget(title="Worker start (s)", left=[nl("ModelLoadSeconds"), nl("WarmupSeconds")]),
            cw.GraphWidget(title="Jobs", left=[nl("JobsSucceeded", "Sum"), nl("JobsFailed", "Sum"),
                                               nl("SpotInterruptions", "Sum")]),
        )
        dash.add_widgets(
            cw.GraphWidget(title="Web", left=[compute.alb.metrics.request_count(),
                                              compute.alb.metrics.http_code_target(elbv2.HttpCodeTarget.TARGET_5XX_COUNT)],
                           right=[compute.service.metric_cpu_utilization()]),
            cw.GraphWidget(title="Aurora ACU", left=[data.db.metric_serverless_database_capacity()]),
        )

        # ─── FIS: Spot interruption of one running worker (Experiment 2) ─────
        fis_role = iam.Role(self, "FisRole", assumed_by=iam.ServicePrincipal("fis.amazonaws.com"))
        fis_role.add_to_policy(iam.PolicyStatement(
            actions=["ec2:SendSpotInstanceInterruptions", "ec2:DescribeInstances"], resources=["*"]))
        fis.CfnExperimentTemplate(
            self, "SpotInterrupt",
            description="Send a Spot interruption notice to one running NeuroLens GPU worker",
            role_arn=fis_role.role_arn,
            stop_conditions=[fis.CfnExperimentTemplate.ExperimentTemplateStopConditionProperty(source="none")],
            targets={"worker": fis.CfnExperimentTemplate.ExperimentTemplateTargetProperty(
                resource_type="aws:ec2:spot-instance",
                selection_mode="COUNT(1)",
                resource_tags={"aws:autoscaling:groupName": ASG_NAME},
                filters=[fis.CfnExperimentTemplate.ExperimentTemplateTargetFilterProperty(
                    path="State.Name", values=["running"])],
            )},
            actions={"interrupt": fis.CfnExperimentTemplate.ExperimentTemplateActionProperty(
                action_id="aws:ec2:send-spot-instance-interruptions",
                parameters={"durationBeforeInterruption": "PT2M"},
                targets={"SpotInstances": "worker"},
            )},
            tags={"Name": "neurolens-spot-interrupt"},
        )

        # ─── Monthly budget ──────────────────────────────────────────────────
        if ctx.get("alert_email"):
            budgets.CfnBudget(self, "Budget", budget=budgets.CfnBudget.BudgetDataProperty(
                budget_type="COST", time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=ctx["budget_usd"], unit="USD")),
                notifications_with_subscribers=[
                    budgets.CfnBudget.NotificationWithSubscribersProperty(
                        notification=budgets.CfnBudget.NotificationProperty(
                            notification_type="ACTUAL", comparison_operator="GREATER_THAN", threshold=pct),
                        subscribers=[budgets.CfnBudget.SubscriberProperty(subscription_type="EMAIL",
                                                                          address=ctx["alert_email"])])
                    for pct in (50, 80, 100)])
