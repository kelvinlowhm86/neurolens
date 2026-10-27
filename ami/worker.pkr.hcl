# NeuroLens GPU worker AMI.
#
# Bakes onto AWS's Deep Learning Base GPU AMI (NVIDIA driver + Docker + NVIDIA Container Toolkit
# already installed by AWS):
#   * the worker image's layers (so boot only pulls the thin code layer)
#   * every model weight under /opt/neurolens/models (so the worker runs with HF_HUB_OFFLINE=1)
#   * the systemd unit + boot script that pull the current image tag and start the worker
#
# Run via `make ami` (Packer runs as a container; nothing is installed on the build machine).

packer {
  required_plugins {
    amazon = {
      version = "~> 1.3"
      source  = "github.com/hashicorp/amazon"
    }
  }
}

variable "region" {
  type    = string
  default = "ap-southeast-1"
}

variable "instance_type" {
  type    = string
  default = "g6e.2xlarge" # bake on the same GPU so the offline warm-up check runs for real
}

variable "worker_image" {
  type        = string
  description = "Full ECR image reference, e.g. 123.dkr.ecr.ap-southeast-1.amazonaws.com/neurolens-worker:abc123"
}

variable "ecr_password" {
  type      = string
  sensitive = true
}

variable "hf_token" {
  type      = string
  sensitive = true
}

data "amazon-ami" "dlami" {
  region      = var.region
  owners      = ["amazon"]
  most_recent = true
  filters = {
    name                = "Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04) *"
    architecture        = "x86_64"
    state               = "available"
    root-device-type    = "ebs"
    virtualization-type = "hvm"
  }
}

source "amazon-ebs" "worker" {
  region        = var.region
  instance_type = var.instance_type
  source_ami    = data.amazon-ami.dlami.id
  ssh_username  = "ubuntu"
  ami_name      = "neurolens-worker-{{timestamp}}"
  ami_description = "NeuroLens GPU worker: TRIBE v2 weights + worker image layers pre-baked"

  launch_block_device_mappings {
    device_name           = "/dev/sda1"
    volume_size           = 150
    volume_type           = "gp3"
    delete_on_termination = true
  }

  tags = {
    project      = "neurolens"
    worker_image = var.worker_image
    source_ami   = "{{ .SourceAMI }}"
  }
}

build {
  sources = ["source.amazon-ebs.worker"]

  provisioner "shell" {
    inline = ["mkdir -p /tmp/neurolens"]
  }

  provisioner "file" {
    source      = "ami/files/"
    destination = "/tmp/neurolens/"
  }

  provisioner "file" {
    source      = ".dev/ami-sample.mp4" # an ad with speech, copied here by `make ami`
    destination = "/tmp/neurolens/sample.mp4"
  }

  provisioner "shell" {
    environment_vars = [
      "WORKER_IMAGE=${var.worker_image}",
      "ECR_PASSWORD=${var.ecr_password}",
      "HF_TOKEN=${var.hf_token}",
    ]
    execute_command = "sudo -E bash '{{ .Path }}'"
    script          = "ami/provision.sh"
  }
}
