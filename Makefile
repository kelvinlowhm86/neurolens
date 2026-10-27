# NeuroLens — one entry point for dev (everything on this machine) and AWS.
# Nothing here installs onto the host: tools go into project venvs; Docker targets only build images.

SHELL := /bin/bash
PYTHON ?= python3.11
DEV_VENV := .venv-dev
WORKER_VENV := .venv-worker
TORCH_INDEX := https://download.pytorch.org/whl/cu126
TAG ?= $(shell git -c safe.directory='*' rev-parse --short HEAD 2>/dev/null || echo dev)

# Keep pip caches and temp files off the root partition.
export TMPDIR := $(CURDIR)/.dev/tmp
export PIP_CACHE_DIR := $(CURDIR)/.dev/pip-cache

.PHONY: help ami ami-validate check-secrets install-hooks restart-web destroy-all venv-dev venv-worker venv-tools quota-check cdk-synth cdk-bootstrap secrets deploy ecr-login push-web push-worker release-web release-worker seed-samples pause teardown dev dev-mock dev-down dev-status dev-logs dev-stripe \
        test test-e2e download-weights silent-check bench-local build-web build-worker build-worker-mock

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

$(TMPDIR) $(PIP_CACHE_DIR):
	@mkdir -p $@

# ─── Environments (venvs only) ──────────────────────────────────────────────

venv-dev: | $(TMPDIR) $(PIP_CACHE_DIR) ## web + moto + mock worker + tests venv
	$(PYTHON) -m venv $(DEV_VENV)
	$(DEV_VENV)/bin/pip install -q --upgrade pip
	$(DEV_VENV)/bin/pip install -r requirements-dev.txt

venv-worker: | $(TMPDIR) $(PIP_CACHE_DIR) ## real GPU worker venv (~8 GB; CUDA comes inside the torch wheels)
	$(PYTHON) -m venv $(WORKER_VENV)
	$(WORKER_VENV)/bin/pip install -q --upgrade pip
	$(WORKER_VENV)/bin/pip install --no-deps -r worker/requirements.lock --extra-index-url $(TORCH_INDEX)
	$(WORKER_VENV)/bin/pip install --no-deps -e packages/common
	$(WORKER_VENV)/bin/python -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.device_count(), 'GPU(s)')"

# ─── Dev mode: the whole system on this machine (APP_ENV=dev) ───────────────

dev: ## start everything with the real GPU worker
	$(DEV_VENV)/bin/python scripts/dev_stack.py up

dev-mock: ## start everything with fake inference (no GPU)
	$(DEV_VENV)/bin/python scripts/dev_stack.py up --mock

dev-down: ## stop everything (Postgres data kept)
	$(DEV_VENV)/bin/python scripts/dev_stack.py down

dev-status: ## what is running
	$(DEV_VENV)/bin/python scripts/dev_stack.py status

dev-logs: ## tail a log: make dev-logs NAME=worker
	$(DEV_VENV)/bin/python scripts/dev_stack.py logs $(NAME)

dev-stripe: ## forward Stripe test webhooks to localhost (Stripe CLI in a container; prints STRIPE_WEBHOOK_SECRET)
	docker run --rm -it --network host -e STRIPE_API_KEY=$$(grep ^STRIPE_SECRET_KEY .env | cut -d= -f2) \
	  stripe/stripe-cli:latest listen --forward-to localhost:8000/api/billing/webhook

# ─── Tests ──────────────────────────────────────────────────────────────────

test: ## unit + integration tests (uses the dev Postgres container: make dev or dev-mock first)
	TEST_DATABASE_URL=postgresql://neurolens:neurolens@localhost:5433/postgres $(DEV_VENV)/bin/pytest -q

test-e2e: ## browser-style end-to-end run against the running dev stack
	set -a && source .env && set +a && $(DEV_VENV)/bin/python scripts/e2e.py $(if $(VIDEO),--video "$(VIDEO)")

# ─── GPU worker tools (Part 5 / Experiment 3) ───────────────────────────────

worker_env = set -a && source .env && set +a && PYTHONPATH=worker

download-weights: ## fetch all weights into MODELS_DIR (needs HF_TOKEN; SAMPLE=ad-with-speech.mp4)
	$(worker_env) HF_HUB_OFFLINE=0 $(WORKER_VENV)/bin/python -m neurolens_worker download-weights --sample "$(SAMPLE)"

silent-check: ## compare SILENT_MODE=events vs ffmpeg on one video: make silent-check VIDEO=...
	$(worker_env) SCRATCH_DIR=$(CURDIR)/.dev/scratch-tools $(WORKER_VENV)/bin/python -m neurolens_worker silent-check "$(VIDEO)"

bench-local: ## Experiment 3 timings on this machine: make bench-local VIDEOS="a.mp4 b.mp4"
	$(worker_env) SCRATCH_DIR=$(CURDIR)/.dev/scratch-tools $(WORKER_VENV)/bin/python -m neurolens_worker bench --out experiments/out/bench-local.csv $(VIDEOS)

# ─── Container images (specs for AWS; building needs Docker storage off the root partition) ─

build-web: ## web image
	docker build -f web/Dockerfile -t neurolens-web:$(TAG) .

build-worker: ## GPU worker image (same pins as .venv-worker)
	docker build -f worker/Dockerfile --target gpu -t neurolens-worker:$(TAG) .

build-worker-mock: ## CPU-only mock worker image
	docker build -f worker/Dockerfile --target mock -t neurolens-worker:$(TAG)-mock .

# ─── AWS (APP_ENV=aws). Tools live in .venv-tools; set AWS_PROFILE first. ───

TOOLS := .venv-tools/bin
REGION ?= ap-southeast-1
AWS := $(TOOLS)/aws --region $(REGION)
# CDK synth runs node via jsii; keep its caches off the root partition.
CDK_ENV := XDG_CACHE_HOME=$(CURDIR)/.dev/cache JSII_RUNTIME_PACKAGE_CACHE_ROOT=$(CURDIR)/.dev/cache/jsii \
           JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1 PATH=$(CURDIR)/$(TOOLS):$$PATH
CDK := cd infra && $(CDK_ENV) cdk
ACCOUNT = $(shell $(AWS) sts get-caller-identity --query Account --output text)
ECR = $(ACCOUNT).dkr.ecr.$(REGION).amazonaws.com
CDK_ARGS ?=

venv-tools: | $(TMPDIR) $(PIP_CACHE_DIR) ## AWS CLI + CDK (app libs and CLI) in a venv
	$(PYTHON) -m venv .venv-tools
	$(TOOLS)/pip install -q --upgrade pip
	$(TOOLS)/pip install -r infra/requirements.txt

quota-check: ## g6e offered? G/VT vCPU quotas?
	PATH=$(CURDIR)/$(TOOLS):$$PATH scripts/quota_check.sh $(REGION)

cdk-synth: ## render CloudFormation templates (no AWS calls)
	$(CDK) synth --quiet $(CDK_ARGS)

cdk-bootstrap: ## once per account/region
	$(CDK) bootstrap aws://$(ACCOUNT)/$(REGION)

secrets: ## .env.deploy → Secrets Manager neurolens/app
	$(TOOLS)/python scripts/put_secrets.py .env.deploy

deploy: ## deploy stacks: make deploy [STACKS="NeuroLens-Network NeuroLens-Data"] CDK_ARGS="-c worker_ami_id=ami-…"
	$(CDK) deploy $(or $(STACKS),--all) --require-approval never $(CDK_ARGS)

ecr-login:
	$(AWS) ecr get-login-password | docker login --username AWS --password-stdin $(ECR)

push-web: build-web ecr-login ## build + push the web image (tag = git sha)
	docker tag neurolens-web:$(TAG) $(ECR)/neurolens-web:$(TAG) && docker push $(ECR)/neurolens-web:$(TAG)

push-worker: build-worker ecr-login ## build + push the GPU worker image
	docker tag neurolens-worker:$(TAG) $(ECR)/neurolens-worker:$(TAG) && docker push $(ECR)/neurolens-worker:$(TAG)

release-web: push-web ## roll the web tier to this commit
	$(MAKE) deploy CDK_ARGS="$(CDK_ARGS) -c web_image_tag=$(TAG)"

restart-web: ## restart the web tasks (picks up changed secrets)
	$(AWS) ecs update-service --cluster $$($(AWS) ecs list-clusters --query 'clusterArns[0]' --output text) \
	  --service $$($(AWS) ecs list-services --cluster $$($(AWS) ecs list-clusters --query 'clusterArns[0]' --output text) \
	  --query 'serviceArns[0]' --output text) --force-new-deployment >/dev/null && echo "web tasks restarting"

release-worker: push-worker ## new workers boot this commit (running ones finish their job first)
	$(AWS) ssm put-parameter --name /neurolens/worker/image_tag --value $(TAG) --overwrite

seed-samples: ## upload the sample gallery to the bucket's samples/ prefix
	$(TOOLS)/python scripts/seed_samples.py $$($(AWS) cloudformation describe-stacks --stack-name NeuroLens-Data \
	  --query "Stacks[0].Outputs[?contains(OutputKey,'Bucket')].OutputValue | [0]" --output text)

pause: ## stop paying for compute between sessions (keeps data)
	$(AWS) autoscaling update-auto-scaling-group --auto-scaling-group-name neurolens-workers --desired-capacity 0
	$(AWS) ecs update-service --cluster $$($(AWS) ecs list-clusters --query 'clusterArns[0]' --output text) \
	  --service $$($(AWS) ecs list-services --cluster $$($(AWS) ecs list-clusters --query 'clusterArns[0]' --output text) \
	  --query 'serviceArns[0]' --output text) --desired-count 0 >/dev/null

teardown: ## destroy compute + ops (keeps bucket, DB, ECR)
	$(CDK) destroy NeuroLens-Ops NeuroLens-Compute --force

destroy-all: ## destroy every stack (asks to confirm; bucket + ECR repos are retained and must be emptied by hand)
	$(CDK) destroy --all

# ─── Worker AMI (Packer runs as a container; the bake itself runs on an AWS GPU instance) ─

PACKER_IMAGE := hashicorp/packer:1.11.2
PACKER = docker run --rm -v $(CURDIR):/work -w /work -v $$HOME/.aws:/root/.aws:ro \
         -e AWS_PROFILE -e AWS_REGION=$(REGION) -e PACKER_PLUGIN_PATH=/work/.dev/packer-plugins \
         -e PKR_VAR_hf_token -e PKR_VAR_ecr_password $(PACKER_IMAGE)

ami-validate: ## check the Packer template (needs AWS credentials for the AMI lookup)
	$(PACKER) init ami/worker.pkr.hcl
	PKR_VAR_hf_token=x PKR_VAR_ecr_password=x $(PACKER) validate -var worker_image=x ami/worker.pkr.hcl

ami: push-worker ## bake the worker AMI: make ami SAMPLE=ad-with-speech.mp4 (HF_TOKEN in .env); prints the AMI id
	@test -n "$(SAMPLE)" || { echo "SAMPLE=<an ad with spoken dialogue> is required"; exit 1; }
	cp "$(SAMPLE)" .dev/ami-sample.mp4
	$(PACKER) init ami/worker.pkr.hcl
	PKR_VAR_hf_token=$$(grep ^HF_TOKEN= .env | cut -d= -f2) \
	PKR_VAR_ecr_password=$$($(AWS) ecr get-login-password) \
	  $(PACKER) build -var region=$(REGION) -var worker_image=$(ECR)/neurolens-worker:$(TAG) ami/worker.pkr.hcl
	$(AWS) ssm put-parameter --name /neurolens/worker/image_tag --value $(TAG) --overwrite

# ─── Git safety: secrets never leave this machine ───────────────────────────

check-secrets: ## scan every file git would see for secrets / blocked files (run before pushing)
	scripts/check_secrets.sh --all

install-hooks: ## local pre-commit hook that refuses commits containing secrets or .env files
	printf '#!/usr/bin/env bash\nexec scripts/check_secrets.sh\n' > $$(git rev-parse --git-path hooks)/pre-commit
	chmod +x $$(git rev-parse --git-path hooks)/pre-commit
	@echo "pre-commit hook installed"
