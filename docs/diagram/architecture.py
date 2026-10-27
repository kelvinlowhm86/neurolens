"""NeuroLens end-state AWS architecture diagram.

Render:  python architecture.py   (needs `pip install diagrams` + graphviz)
Output:  ../architecture.png
"""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import EC2Ami, EC2AutoScaling, EC2ContainerRegistry, EC2SpotInstance, Fargate, Lambda
from diagrams.aws.cost import Budgets
from diagrams.aws.database import Aurora
from diagrams.aws.integration import Eventbridge, SimpleNotificationServiceSns, SimpleQueueServiceSqsQueue
from diagrams.aws.management import (
    CloudwatchAlarm,
    Cloudwatch,
    SystemsManagerParameterStore,
    TrustedAdvisorChecklistFaultTolerant,
)
from diagrams.aws.network import CloudFront, ElbApplicationLoadBalancer, Endpoint
from diagrams.aws.security import SecretsManager
from diagrams.aws.storage import SimpleStorageServiceS3Bucket
from diagrams.aws.general import Client, GenericSamlToken, GenericSDK, Users

GRAPH = {
    "fontsize": "22",
    "fontname": "Helvetica-Bold",
    "pad": "0.6",
    "nodesep": "0.55",
    "ranksep": "1.0",
    "splines": "spline",
    "compound": "true",
}
NODE = {"fontsize": "12", "fontname": "Helvetica"}
EDGE = {"fontsize": "11", "fontname": "Helvetica"}

DATA = "#1f6fb2"      # user / data path
CONTROL = "#2e7d32"   # control + scaling
BILLING = "#b26a00"   # billing integrity
OPS = "#7a7a7a"       # config, logs, ops


def e(label="", color=DATA, style="solid", **kw):
    return Edge(label=label, color=color, fontcolor=color, style=style, **kw)


with Diagram(
    "NeuroLens — End-State AWS Architecture (ap-southeast-1)",
    filename="../architecture",
    outformat="png",
    direction="LR",
    show=False,
    graph_attr=GRAPH,
    node_attr=NODE,
    edge_attr=EDGE,
):
    with Cluster("Clients & 3rd parties"):
        browser = Client("Browser SPA\n(vanilla JS + Chart.js)")
        google = GenericSamlToken("Google OAuth2\n(OIDC SSO)")
        stripe = GenericSDK("Stripe\nhosted Checkout\n(test mode)")

    dev = Users("Team (Makefile)\nbuild / push / deploy\nCDK · Packer")

    with Cluster("AWS Cloud · ap-southeast-1"):
        cdn = CloudFront("CloudFront\nTLS · *.cloudfront.net\n+ X-Origin-Verify header")

        with Cluster("VPC (2 AZs, no NAT gateway)"):
            with Cluster("Public subnets"):
                alb = ElbApplicationLoadBalancer("Application\nLoad Balancer\n(CloudFront prefix list only)")
                with Cluster("ECS cluster"):
                    web = Fargate("Fargate service: web\nFlask + gunicorn\n1–3 tasks (stateless)")
                with Cluster("EC2 Auto Scaling Group · Spot g6e.2xlarge (L40S 48 GB) · min 0 / max N"):
                    asg = EC2AutoScaling("ASG\nstep scale-out on queue\n+ pre-warm nudge")
                    worker = EC2SpotInstance("GPU worker\ncustom AMI + worker container\nTRIBE v2 dual pass + ROI")
            with Cluster("Isolated subnets"):
                db = Aurora("Aurora PostgreSQL\nServerless v2 (0–2 ACU)\nusers · jobs · ledger")
            s3ep = Endpoint("S3 gateway endpoint\n→ S3 bucket\n(no NAT, no data charge)")

        bucket = SimpleStorageServiceS3Bucket("S3 bucket\nuploads/ (48 h)\nresults/ (30 d) · samples/")

        with Cluster("Async task queue"):
            queue = SimpleQueueServiceSqsQueue("SQS jobs\nvisibility 300 s")
            dlq = SimpleQueueServiceSqsQueue("SQS DLQ\nafter 2 receives")

        with Cluster("Billing integrity (serverless)"):
            refund = Lambda("Lambda\ndlq_refund")
            sched = Eventbridge("EventBridge\nrate(5 min)")
            reaper = Lambda("Lambda\nsla_reaper")

        with Cluster("Config, images & secrets  (read by web · worker · Lambdas via IAM roles)"):
            ecr = EC2ContainerRegistry("ECR\nneurolens-web\nneurolens-worker")
            ami = EC2Ami("Worker AMI\n(weights + image\nlayers pre-baked)")
            secrets = SecretsManager("Secrets Manager\nneurolens/db\nneurolens/app")
            ssm = SystemsManagerParameterStore("SSM Parameters\nworker image_tag\nlast_job_created_at")

        with Cluster("Observability, cost & experiments  (all compute ships logs + EMF metrics)"):
            alarm = CloudwatchAlarm("CloudWatch alarm\nqueue depth ≥ 1")
            cw = Cloudwatch("CloudWatch\nlogs · EMF metrics\ndashboard")
            sns = SimpleNotificationServiceSns("SNS\nemail alerts")
            budget = Budgets("AWS Budgets")
            fis = TrustedAdvisorChecklistFaultTolerant("AWS FIS\nSpot interruption\n(Exp 2)")

    nc = {"constraint": "false"}  # back-edges: don't let them drive the layout

    # ── user / data path ──────────────────────────────────────────────
    browser >> e("1 HTTPS: SPA · API · OAuth · 5 s polling") >> cdn
    cdn >> e("HTTP + secret header") >> alb >> e() >> web
    browser >> e("2 presigned POST upload\n(bypasses web tier)", style="bold") >> bucket
    bucket >> e("3 ObjectCreated uploads/") >> queue
    queue >> e("4 long poll / claim") >> worker
    worker >> e("5 download input · write result.json\n(to S3 bucket, private path)") >> s3ep
    web >> e("presign · read result", **nc) >> bucket

    # ── control plane ─────────────────────────────────────────────────
    web >> e("pre-warm SetDesiredCapacity\n(when upload starts)", CONTROL, "bold") >> asg
    asg >> e("launch / idle self-terminate", CONTROL) >> worker
    queue >> e("queue depth", CONTROL, "dashed") >> alarm
    alarm >> e("step scale-out (backup)", CONTROL, "dashed", **nc) >> asg
    fis >> e("inject Spot interruption", CONTROL, "dashed", **nc) >> worker
    worker >> e("Spot requeue", CONTROL, "dashed", **nc) >> queue

    # ── state + billing ───────────────────────────────────────────────
    web >> e("reserve credits · create job", BILLING) >> db
    worker >> e("atomic claim · heartbeat · capture", BILLING) >> db
    queue >> e("redrive after 2", BILLING) >> dlq >> e(color=BILLING) >> refund
    sched >> e(color=BILLING) >> reaper
    [refund, reaper] >> e("RDS Data API · release / refund", BILLING) >> db
    web >> e("Checkout session", BILLING, **nc) >> stripe
    stripe >> e("signed webhook", BILLING, "dashed", **nc) >> cdn
    web >> e("OIDC login", BILLING, "dashed", **nc) >> google

    # ── build & release ───────────────────────────────────────────────
    dev >> e("make push", OPS, "dashed") >> ecr
    dev >> e("make ami (Packer)", OPS, "dashed") >> ami
    ami >> e("launch template", OPS, "dashed") >> asg
    ecr >> e("pull thin code layer", OPS, "dashed") >> worker
    worker >> e(color=OPS, style="dotted") >> cw
    cw >> e(color=OPS, style="dotted") >> sns
    budget >> e(color=OPS, style="dotted") >> sns
