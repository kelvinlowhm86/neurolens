# ─── Private network for the GPU workers (M2a §4a) ─────────────────────────
#
#   public subnets (zone A + build-only b, c, d): NAT Gateway, image builds; route to the internet gateway
#   private subnets (zones A-D): workers, no public IP; route to the NAT Gateway while it exists
#   S3 gateway endpoint on both route tables: S3 traffic never goes through the NAT Gateway
#
# The NAT Gateway is a switch (var.nat_gateway, M3b §5): on for days with GPU work and the deployed
# window, off otherwise (about $1.20 a day while it exists). Without it the workers reach only S3, so
# they must not run: start_work.sh refuses to start them, and Terraform refuses to remove the gateway
# while the worker group may start any (below). One gateway, not one per zone: a zone outage during a
# work session is unlikely, and AWS's per-zone advice is for zone resilience we do not need.

resource "aws_vpc" "main" {
  cidr_block           = "10.20.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "neurolens-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "neurolens-igw" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.20.0.0/24"
  availability_zone       = var.zones[0]
  map_public_ip_on_launch = true

  tags = { Name = "neurolens-public" }
}

# More public subnets, used only by infra/build_ami.sh: a GPU type can be sold out in one zone
# (InsufficientInstanceCapacity), so the build tries each zone in turn. Free; nothing runs in them.
resource "aws_subnet" "public_build" {
  count                   = length(var.build_extra_zones)
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.20.${1 + count.index}.0/24"
  availability_zone       = var.build_extra_zones[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "neurolens-public-build-${count.index}" }
}

resource "aws_route_table_association" "public_build" {
  count          = length(var.build_extra_zones)
  subnet_id      = aws_subnet.public_build[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_subnet" "private" {
  count             = length(var.zones)
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.20.${10 + count.index}.0/24"
  availability_zone = var.zones[count.index]

  tags = { Name = "neurolens-private-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "neurolens-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  # The way out while the NAT Gateway exists; none otherwise. An explicit list, even empty: leaving
  # `route` out would tell Terraform to ignore routes, so an old one would stay behind. In this
  # attribute form every route field must be named.
  route = [for id in aws_nat_gateway.main[*].id : {
    cidr_block                 = "0.0.0.0/0"
    nat_gateway_id             = id
    ipv6_cidr_block            = null
    destination_prefix_list_id = null
    carrier_gateway_id         = null
    core_network_arn           = null
    egress_only_gateway_id     = null
    gateway_id                 = null
    local_gateway_id           = null
    network_interface_id       = null
    odb_network_arn            = null
    transit_gateway_id         = null
    vpc_endpoint_id            = null
    vpc_peering_connection_id  = null
  }]

  tags = { Name = "neurolens-private" }

  lifecycle {
    # Workers running without a way out cannot fetch jobs or reach the database, and would only
    # bill until the idle alarm. The group's max is read at plan time.
    precondition {
      condition     = var.nat_gateway || (alltrue([for g in data.aws_autoscaling_group.workers : g.max_size == 0]) && length(data.aws_instances.workers.ids) == 0)
      error_message = "nat_gateway = false while the worker group may start workers or a worker is still running: run infra/stop_work.sh first, check that no worker is listed, then apply again."
    }
  }
}

# Workers that exist right now, whatever the group says (one may be finishing a job).
data "aws_instances" "workers" {
  filter {
    name   = "tag:Role"
    values = ["worker"]
  }
  filter {
    name   = "tag:Project"
    values = ["neurolens"]
  }
  instance_state_names = ["pending", "running"]
}

# The worker group, when it exists (it does only once an image is set).
data "aws_autoscaling_groups" "workers" {
  names = [local.worker_asg]
}

data "aws_autoscaling_group" "workers" {
  for_each = toset(data.aws_autoscaling_groups.workers.names)
  name     = each.value
}

resource "aws_route_table_association" "private" {
  count          = length(var.zones)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# Free. Videos, code and the ~20 GB of weights go straight to S3 (also from the public subnet, so
# the image build's upload stays on AWS's network).
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id, aws_route_table.public.id]

  tags = { Name = "neurolens-s3" }
}

# ─── Security groups ───────────────────────────────────────────────────────

# Workers and the image-build instance: nothing can connect in; they only call out.
resource "aws_security_group" "no_inbound" {
  name        = "neurolens-no-inbound"
  description = "NeuroLens workers and build instance: no inbound, all outbound"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "neurolens-no-inbound" }
}

# ─── NAT Gateway (on only while var.nat_gateway is true) ──────────────────

resource "aws_eip" "nat" {
  count  = var.nat_gateway ? 1 : 0
  domain = "vpc"
  tags   = { Name = "neurolens-nat", Milestone = "M3b" }
}

resource "aws_nat_gateway" "main" {
  count         = var.nat_gateway ? 1 : 0
  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public.id
  tags          = { Name = "neurolens-nat", Milestone = "M3b" }

  depends_on = [aws_internet_gateway.main]
}
