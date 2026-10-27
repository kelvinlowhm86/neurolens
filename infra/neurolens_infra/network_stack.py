"""
VPC with no NAT gateway: web tasks and GPU workers sit in public subnets with no inbound rules
(outbound only), Aurora sits in isolated subnets. S3 traffic uses a free gateway endpoint.
Skipping NAT saves ~$45/month per AZ — most of the platform's idle baseline.
"""

from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from constructs import Construct


class NetworkStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, cloudfront_prefix_list_id: str | None, **kw):
        super().__init__(scope, cid, **kw)

        self.vpc = ec2.Vpc(
            self, "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
                ec2.SubnetConfiguration(name="db", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24),
            ],
        )
        self.vpc.add_gateway_endpoint("S3", service=ec2.GatewayVpcEndpointAwsService.S3)

        self.alb_sg = ec2.SecurityGroup(self, "AlbSg", vpc=self.vpc, description="ALB: HTTP from CloudFront only")
        if cloudfront_prefix_list_id:
            self.alb_sg.add_ingress_rule(ec2.Peer.prefix_list(cloudfront_prefix_list_id), ec2.Port.tcp(80))
        else:
            # Without the prefix list id, the ALB listener's secret-header rule is the gate (403 otherwise).
            self.alb_sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80))

        self.web_sg = ec2.SecurityGroup(self, "WebSg", vpc=self.vpc, description="Web tasks: from ALB only")
        self.web_sg.add_ingress_rule(self.alb_sg, ec2.Port.tcp(8000))

        self.worker_sg = ec2.SecurityGroup(self, "WorkerSg", vpc=self.vpc, description="GPU workers: no inbound")

        self.db_sg = ec2.SecurityGroup(self, "DbSg", vpc=self.vpc, description="Aurora: from web + workers")
        self.db_sg.add_ingress_rule(self.web_sg, ec2.Port.tcp(5432))
        self.db_sg.add_ingress_rule(self.worker_sg, ec2.Port.tcp(5432))
