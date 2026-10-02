# ─── Private network for the GPU workers (M2a §4a) ─────────────────────────
#
#   public subnet  (zone A): NAT instance, image-build instance; route to the internet gateway
#   private subnets (zones A and B): workers, no public IP; route to the NAT instance
#   S3 gateway endpoint on both route tables: S3 traffic never goes through the NAT instance
#
# One NAT instance, not one per zone: a zone outage during short work sessions is unlikely, and a
# second NAT doubles the moving parts. For zone resilience AWS recommends a managed NAT Gateway per
# zone; a single NAT is the usual trade-off when rare downtime is acceptable.

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
  count             = 2
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

  route {
    cidr_block           = "0.0.0.0/0"
    network_interface_id = aws_instance.nat.primary_network_interface_id
  }

  tags = { Name = "neurolens-private" }
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# Free. Videos, code and the ~20 GB of weights go straight to S3 (also from the public subnet, so
# the image build's upload stays on AWS's network).
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.us-east-1.s3"
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

# The NAT instance accepts traffic only from inside the VPC, to forward it out.
resource "aws_security_group" "nat" {
  name        = "neurolens-nat"
  description = "NeuroLens NAT instance: inbound from the VPC only"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "neurolens-nat" }
}

# ─── NAT instance ──────────────────────────────────────────────────────────

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_instance" "nat" {
  ami                    = data.aws_ssm_parameter.al2023_arm64.value
  instance_type          = "t4g.micro"
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.nat.id]
  iam_instance_profile   = aws_iam_instance_profile.nat.name
  source_dest_check      = false # it forwards other machines' traffic

  # An auto-assigned public IP (from the subnet), not an Elastic IP: it is released while the
  # instance is stopped, so nothing is billed for it then.
  associate_public_ip_address = true

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    volume_size = 8
    volume_type = "gp3"
    encrypted   = true
  }

  # Runs on the first boot only, so everything it sets must survive a stop/start.
  user_data = <<-EOF
    #!/bin/bash
    set -euo pipefail
    dnf install -y iptables-services
    echo "net.ipv4.ip_forward = 1" > /etc/sysctl.d/90-nat.conf
    sysctl -p /etc/sysctl.d/90-nat.conf
    IFACE=$(ip route show default | awk '{print $5}')
    iptables -t nat -A POSTROUTING -o "$IFACE" -s ${aws_vpc.main.cidr_block} -j MASQUERADE
    iptables-save > /etc/sysconfig/iptables
    systemctl enable --now iptables
  EOF

  tags = { Name = "neurolens-nat", Role = "nat" }

  lifecycle {
    # A newer Amazon Linux release must not replace a working NAT instance. A stopped instance
    # reports no public IP, which would otherwise force a replacement on every apply between
    # sessions (the subnet assigns a new one on each start).
    ignore_changes = [ami, associate_public_ip_address]
  }
}
