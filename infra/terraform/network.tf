# ─── Private network for Aurora (M3e §9) ──────────────────────────────────
#
# Only the database lives here: its subnet group needs subnets in at least two availability
# zones. Nothing else uses the VPC. SageMaker runs in AWS's own network, and the container
# reaches S3 and the Data API over their public HTTPS endpoints, so there is no NAT Gateway,
# internet gateway, route table or S3 gateway endpoint any more.

resource "aws_vpc" "main" {
  cidr_block           = "10.20.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "neurolens-vpc" }
}

# One private subnet per zone. No route to the internet: the database is reached only through
# the Data API (HTTPS, IAM-checked), never over port 5432.
resource "aws_subnet" "private" {
  count             = length(var.zones)
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.20.${10 + count.index}.0/24"
  availability_zone = var.zones[count.index]

  tags = { Name = "neurolens-private-${count.index}" }
}
