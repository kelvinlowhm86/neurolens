"""infra/setup.sh and infra/teardown.sh against fakes.

Both scripts run under bash in a temporary copy of the repo, with fake `aws`, `terraform`, `git`
and `sleep` first on PATH. The fakes keep the world in a JSON file, log every call, and fail
loudly (exit 99) on an unknown AWS call. The scripts and their sibling build/deploy scripts are
copied or stubbed so the real infra files are never touched.

Teardown deletes data with no undo: a wrong confirmation, an account mismatch, an unproven stop
or a failed destroy must stop it before the next step. Setup is one command after
the human-only work: it lists every missing prerequisite before changing anything, then runs the
steps in order and skips finished work on a second run.
"""

import importlib.util
import io
import json
import os
import pty
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INFRA = REPO / "infra"

ACCOUNT = "111111111111"
STATE_BUCKET = f"neurolens-tfstate-{ACCOUNT}"
DATA_BUCKET = f"neurolens-data-{ACCOUNT}"

SSM_SECRETS = [
    "/neurolens/terraform/google_client_secret",
    "/neurolens/web/flask_secret_key",
    "/neurolens/web/cognito_client_secret",
    "/neurolens/web/youtube_api_key",
]

AWS_STUB = r"""#!/usr/bin/env python3
import json, os, re, sys

args = sys.argv[1:]
world_path = os.environ["FAKE_WORLD"]
w = json.load(open(world_path))
open(os.environ["FAKE_LOG"], "a").write(json.dumps(args) + "\n")


def save():
    json.dump(w, open(world_path, "w"))


def opt(name, default=None):
    return args[args.index(name) + 1] if name in args else default


def out(text=""):
    print(text)
    save()
    sys.exit(0)


def fail(text, code=254):
    print(text, file=sys.stderr)
    save()
    sys.exit(code)


def has_query(fragment):
    return fragment in opt("--query", "")


def bucket_exists(name):
    return name in w.get("buckets", []) or name == w.get("state_bucket")


key = " ".join(args[:2])
if key in w.get("denied", []) or " ".join(args) in w.get("denied", []):
    fail("An error occurred (AccessDenied) when calling the " + key + " operation", 254)
if key in w.get("fail", []) or " ".join(args) in w.get("fail", []):
    fail("fake aws: injected failure for " + key, 255)

if key == "sts get-caller-identity":
    out(w.get("account", "123456789012"))

if key == "s3api create-bucket":
    w.setdefault("buckets", []).append(opt("--bucket"))
    out()

if key in ("s3api head-bucket", "s3api get-bucket-location", "s3api get-bucket-versioning"):
    if bucket_exists(opt("--bucket")):
        out()
    fail("An error occurred (404) when calling the " + args[1] + " operation: Not Found")

if key in (
    "s3api put-bucket-versioning",
    "s3api put-public-access-block",
    "s3api put-bucket-acl",
    "s3api put-bucket-encryption",
    "s3api put-bucket-tagging",
    "s3api put-bucket-ownership-controls",
    "s3api get-public-access-block",
    "s3api get-bucket-policy",
):
    out()

if key == "s3 ls":
    target = next((a for a in args[2:] if not a.startswith("-")), "")
    bucket = target.removeprefix("s3://").split("/", 1)[0]
    if not bucket_exists(bucket):
        fail("An error occurred (NoSuchBucket) when calling the ListObjectsV2 operation", 1)
    prefix = target.removeprefix(f"s3://{bucket}").lstrip("/")
    for obj in w.get("objects", []):
        if obj["bucket"] == bucket and obj["key"].startswith(prefix):
            print(f"2020-01-01 00:00:00 {obj['size']:>10} {obj['key']}")
    out()

if key == "s3api delete-bucket":
    bucket = opt("--bucket")
    w["buckets"] = [b for b in w.get("buckets", []) if b != bucket]
    w.setdefault("deleted_buckets", []).append(bucket)
    out()

if key in ("s3 cp", "s3 sync"):
    value_options = {
        "--acl", "--cache-control", "--content-disposition", "--content-encoding",
        "--content-language", "--content-type", "--exclude", "--expected-size", "--include",
        "--metadata", "--page-size", "--request-payer", "--sse", "--sse-kms-key-id",
        "--storage-class", "--source-region", "--region", "--profile", "--query", "--output",
    }
    positionals = []
    i = 2
    while i < len(args):
        arg = args[i]
        if arg in value_options:
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        positionals.append(arg)
        i += 1
    if len(positionals) >= 2:
        dest = positionals[1]
        if not dest.startswith("s3://"):
            os.makedirs(dest, exist_ok=True)
            open(os.path.join(dest, ".fake-copy"), "w").write("backup\n")
    out()

if key == "ssm get-parameter":
    if opt("--name") in w.get("ssm_parameters", []):
        out("fake-value")
    fail("An error occurred (ParameterNotFound) when calling the GetParameter operation")

if key == "ssm put-parameter":
    name = opt("--name")
    if name not in w.setdefault("ssm_parameters", []):
        w["ssm_parameters"].append(name)
    w.setdefault("ssm_values", {})[name] = opt("--value")
    out()

if key == "cognito-idp describe-user-pool-client":
    secret = w.get("cognito_client_secret")
    if secret is None:
        fail("An error occurred (ResourceNotFoundException) when calling DescribeUserPoolClient")
    if has_query("ClientSecret"):
        out(secret)
    out(json.dumps({"UserPoolClient": {"ClientSecret": secret}}))

if key in ("ssm describe-parameters", "ssm get-parameters-by-path"):
    names = list(w.get("ssm_parameters", []))
    if key == "ssm get-parameters-by-path":
        path = opt("--path", "/")
        names = [n for n in names if n.startswith(path)]
    else:
        value = re.search(r"Values=([^,]+)", opt("--parameter-filters", ""))
        if value and not value.group(1).endswith("/"):
            names = [n for n in names if n == value.group(1)]
    if has_query("length("):
        out(str(len(names)))
    if has_query("Name"):
        out("\t".join(names))
    out(json.dumps({"Parameters": [{"Name": n} for n in names]}))

if key == "ssm delete-parameter":
    name = opt("--name")
    w["ssm_parameters"] = [n for n in w.get("ssm_parameters", []) if n != name]
    w.setdefault("deleted_parameters", []).append(name)
    out()

if key == "ssm delete-parameters":
    names = [a for a in args[args.index("--names") + 1 :] if not a.startswith("-")]
    w["ssm_parameters"] = [n for n in w.get("ssm_parameters", []) if n not in names]
    w.setdefault("deleted_parameters", []).extend(names)
    out()

if key == "resourcegroupstaggingapi get-resources":
    arns = list(w.get("tagged", []))
    if "--resource-type-filters" in args:
        i = args.index("--resource-type-filters") + 1
        kinds = []
        while i < len(args) and not args[i].startswith("-"):
            kinds.append(args[i].split(":", 1)[-1])
            i += 1
        arns = [a for a in arns if any(f"/{kind}/" in a for kind in kinds)]
    if has_query("length("):
        out(str(len(arns)))
    if has_query("ResourceARN"):
        out("\t".join(arns))
    out(json.dumps({"ResourceTagMappingList": [{"ResourceARN": a} for a in arns]}))

if key == "sagemaker list-associations":
    source = opt("--source-arn")
    destination = opt("--destination-arn")
    pairs = [
        a
        for a in w.get("associations", [])
        if (source and a["source"] == source) or (destination and a["destination"] == destination)
    ]
    query = opt("--query", "")
    if has_query("length("):
        out(str(len(pairs)))
    if "AssociationSummaries[].[" in query:
        for pair in pairs:
            print(pair["source"] + "\t" + pair["destination"])
        out()
    if "SourceArn" in query:
        out("\t".join(pair["source"] for pair in pairs))
    if "DestinationArn" in query:
        out("\t".join(pair["destination"] for pair in pairs))
    out(
        json.dumps(
            {
                "AssociationSummaries": [
                    {"SourceArn": p["source"], "DestinationArn": p["destination"]} for p in pairs
                ]
            }
        )
    )

if key == "sagemaker delete-association":
    source = opt("--source-arn")
    destination = opt("--destination-arn")
    before = len(w.get("associations", []))
    w["associations"] = [
        a
        for a in w.get("associations", [])
        if not (a["source"] == source and a["destination"] == destination)
    ]
    if len(w["associations"]) == before:
        fail("An error occurred (ResourceNotFound) when calling the DeleteAssociation operation")
    out()

if key in ("sagemaker delete-action", "sagemaker delete-context"):
    kind = "action" if key.endswith("delete-action") else "context"
    name = opt("--action-name") if kind == "action" else opt("--context-name")
    wanted = name.rsplit("/", 1)[-1] if name else name
    arn = next((a for a in w.get("tagged", []) if a.endswith(f":{kind}/{wanted}")), None)
    if wanted in w.get("lineage_fail", []) or kind in w.get("lineage_fail", []):
        fail("An error occurred (AccessDenied) when calling the Delete" + kind.title())
    if arn is None:
        fail("An error occurred (ResourceNotFound) when calling the Delete" + kind.title())
    if any(a["source"] == arn or a["destination"] == arn for a in w.get("associations", [])):
        fail("Cannot delete entity with associations")
    w["tagged"] = [a for a in w.get("tagged", []) if a != arn]
    out()

if key == "secretsmanager list-secrets":
    names = list(w.get("secrets", []))
    if has_query("length("):
        out(str(len(names)))
    if has_query("Name"):
        out("\t".join(names))
    out(json.dumps({"SecretList": [{"Name": n} for n in names]}))

for service, command, field, json_key, bucket in (
    (
        "sagemaker",
        "list-endpoint-configs",
        "EndpointConfigName",
        "EndpointConfigs",
        "endpoint_configs",
    ),
    ("sagemaker", "list-models", "ModelName", "Models", "models"),
    ("sagemaker", "list-endpoints", "EndpointName", "Endpoints", "endpoints"),
):
    if key == service + " " + command:
        names = list(w.get(bucket, []))
        if has_query("length("):
            out(str(len(names)))
        if has_query(field):
            out("\t".join(names))
        out(json.dumps({json_key: [{field: n} for n in names]}))

if key == "iam list-roles":
    names = list(w.get("iam_roles", []))
    if has_query("length("):
        out(str(len(names)))
    if has_query("RoleName"):
        out("\t".join(names))
    out(json.dumps({"Roles": [{"RoleName": n} for n in names]}))

target = w.setdefault("target", {"min": 0, "max": 0})
if key == "application-autoscaling delete-scheduled-action":
    name = opt("--scheduled-action-name")
    w["scheduled"] = [a for a in w.get("scheduled", []) if a.get("name") != name]
    out()

if key == "application-autoscaling register-scalable-target":
    if opt("--min-capacity") is not None:
        target["min"] = int(opt("--min-capacity"))
    if opt("--max-capacity") is not None:
        target["max"] = int(opt("--max-capacity"))
    out()

if key == "application-autoscaling describe-scalable-targets":
    if has_query("length("):
        out("1")
    shown = w.get("readback", target)
    out(f'{shown["min"]}\t{shown["max"]}')

if key == "sagemaker update-endpoint-weights-and-capacities":
    desired = opt("--desired-weights-and-capacities", "")
    match = re.search(r"DesiredInstanceCount=(\d+)", desired)
    if match:
        w["desired"] = int(match.group(1))
        if w["desired"] == 0 and not w.get("stuck"):
            w["instances"] = 0
    out()

if key == "sagemaker describe-endpoint":
    if w.get("endpoint_missing"):
        fail(
            "An error occurred (ValidationException) when calling the DescribeEndpoint "
            "operation: Could not find endpoint"
        )
    if has_query("endpointstatus") or has_query("EndpointStatus"):
        status = w.get("status", "InService")
        out(f"{status}\t{w.get('desired', w.get('instances', 0))}\t{w.get('instances', 0)}")
    out()

print("fake aws: unexpected call: " + " ".join(args), file=sys.stderr)
sys.exit(99)
"""

TERRAFORM_STUB = r"""#!/usr/bin/env python3
import json, os, sys

args = sys.argv[1:]
world_path = os.environ["FAKE_WORLD"]
w = json.load(open(world_path))

value_flags = {
    "-chdir", "-var", "-var-file", "-backend-config", "-out", "-target",
    "-parallelism", "-lock-timeout", "-input", "-refresh",
}
cmd = None
i = 0
while i < len(args):
    arg = args[i]
    if arg in value_flags:
        i += 2
        continue
    if arg.startswith("-"):
        i += 1
        continue
    cmd = arg
    break

open(os.environ["FAKE_LOG"], "a").write(json.dumps(["terraform", cmd or "", *args]) + "\n")


def save():
    json.dump(w, open(world_path, "w"))


def out(text=""):
    print(text)
    save()
    sys.exit(0)


def fail(text, code=1):
    print(text, file=sys.stderr)
    save()
    sys.exit(code)


if "terraform" in w.get("fail", []) or "terraform " + (cmd or "") in w.get("fail", []):
    fail("fake terraform: injected failure for " + (cmd or "terraform"))

if cmd == "output":
    if not w.get("terraform_state", True):
        fail("No state file was found!")
    if w.get("terraform_state_empty", False):
        fail("No outputs found!")
    name = None
    j = args.index("output") + 1
    while j < len(args):
        if not args[j].startswith("-"):
            name = args[j]
            break
        j += 1
    data = {
        "region": w.get("region", "us-east-1"),
        "account_id": w.get("terraform_account", w.get("account", "123456789012")),
        "bucket_name": w.get("bucket", "neurolens-data-123456789012"),
        "gpu_endpoint": w.get("endpoint", "neurolens"),
        "gpu_variant": w.get("variant", "main"),
        "max_instances": str(w.get("max_instances", 1)),
        "idle_stop_minutes": str(w.get("idle_stop_minutes", 7)),
        "worker_image": w.get("worker_image", ""),
        "cloudfront_distribution_id": "FAKEDIST",
        "worker_ecr_repository": "123456789012.dkr.ecr.us-east-1.amazonaws.com/neurolens-worker",
        "worker_image_build_project": "neurolens-worker-image",
        "db_cluster_arn": "arn:aws:rds:us-east-1:123456789012:cluster:fake",
        "db_secret_arn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:fake",
        "db_name": "neurolens",
        "cognito_client_id": "fake-client-id",
        "cognito_user_pool_id": "us-east-1_FAKEPOOL",
    }
    if "-json" in args or "--json" in args:
        out(json.dumps(data))
    if name is None:
        for key, value in data.items():
            print(key + " = " + value)
        save()
        sys.exit(0)
    out(data.get(name, ""))

if cmd == "state":
    if not w.get("terraform_state", True):
        fail("No state file was found!")
    if w.get("terraform_state_empty", False):
        out("")
    out("aws_s3_bucket.main")

if cmd in ("init", "plan", "apply", "destroy", "validate", "fmt", "import"):
    if cmd == "plan":
        code = int(w.get("plan_exit", 0))
        if code == 2:
            print("Plan: 1 to add, 0 to change, 0 to destroy.")
            save()
            sys.exit(2)
        if code:
            print("Error: failed to plan", file=sys.stderr)
            save()
            sys.exit(code)
    if cmd == "apply":
        w["terraform_state"] = True
        w["terraform_state_empty"] = False
        bucket = w.get("bucket")
        if bucket and bucket not in w.setdefault("buckets", []):
            w["buckets"].append(bucket)
    if cmd == "destroy":
        w["terraform_state"] = True
        w["terraform_state_empty"] = True
    save()
    out("Terraform " + cmd + " complete.")

out()
"""

GIT_STUB = r"""#!/usr/bin/env python3
import os, sys

args = sys.argv[1:]
if "--show-toplevel" in args:
    print(os.environ["FAKE_REPO"])
elif "rev-parse" in args:
    print("abcdef123456")
"""

SLEEP_STUB = '#!/usr/bin/env bash\necho sleep >> "$FAKE_LOG.sleeps"\n'

STEP_SH = r"""#!/usr/bin/env bash
python3 - "$@" <<'PY'
import json, os, sys

name = "__NAME__"
args = sys.argv[1:]
open(os.environ["FAKE_LOG"], "a").write(json.dumps(["step", name, *args]) + "\n")
world = json.load(open(os.environ["FAKE_WORLD"]))
if name in world.get("fail", []) or "step " + name in world.get("fail", []):
    print("fake step failed: " + name, file=sys.stderr)
    sys.exit(1)
root = os.environ.get("FAKE_REPO", os.getcwd())


def touch(rel):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write("fake")


if name == "build_web_lambda":
    touch("build/web_lambda.zip")
elif name == "build_ffprobe_layer":
    touch("infra/terraform/.build/ffprobe_layer.zip")
elif name == "build_report_layer":
    touch("infra/terraform/.build/report_layer.zip")
elif name == "build_image":
    print("Digest: sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
PY
"""

STEP_PY = r"""#!/usr/bin/env python3
import json, os, sys

name = "__NAME__"
args = sys.argv[1:]
open(os.environ["FAKE_LOG"], "a").write(json.dumps(["step", name, *args]) + "\n")
world = json.load(open(os.environ["FAKE_WORLD"]))
if name in world.get("fail", []) or "step " + name in world.get("fail", []):
    print("fake step failed: " + name, file=sys.stderr)
    sys.exit(1)
if name == "upload_model_data" and not world.get("upload_leaves_empty"):
    world.setdefault("objects", []).append(
        {"bucket": world.get("bucket", ""), "key": "model-data/models/hub/refs/main", "size": 4}
    )
    json.dump(world, open(os.environ["FAKE_WORLD"], "w"))
"""


def teardown_world(**overrides):
    world = {
        "account": ACCOUNT,
        "terraform_account": ACCOUNT,
        "terraform_state": True,
        "terraform_state_empty": False,
        "region": "us-east-1",
        "bucket": DATA_BUCKET,
        "buckets": [STATE_BUCKET],
        "target": {"min": 0, "max": 0},
        "instances": 0,
        "desired": 0,
        "status": "InService",
        "scheduled": [],
        "tagged": [],
        "secrets": [],
        "endpoint_configs": [],
        "models": [],
        "endpoints": [],
        "ssm_parameters": list(SSM_SECRETS),
        "iam_roles": [],
        "associations": [],
        "lineage_fail": [],
        "plan_exit": 0,
        "denied": [],
        "fail": [],
    }
    world.update(overrides)
    return world


def setup_world(**overrides):
    world = {
        "account": ACCOUNT,
        "terraform_account": ACCOUNT,
        "terraform_state": True,
        "region": "us-east-1",
        "bucket": DATA_BUCKET,
        "buckets": [STATE_BUCKET, DATA_BUCKET],
        "ssm_parameters": list(SSM_SECRETS),
        "cognito_client_secret": "fake-cognito-secret",
        "objects": [
            {"bucket": DATA_BUCKET, "key": "model-data/models/hub/refs/main", "size": 4},
        ],
        "worker_image": "sha256:" + "a" * 64,
        "fail": [],
    }
    world.update(overrides)
    return world


class Run:
    def __init__(self, code, stdout, stderr, log):
        self.code = code
        self.out = stdout
        self.err = stderr
        self.calls = (
            [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        )

    @property
    def text(self):
        return self.out + "\n" + self.err

    def find(self, service, command):
        return [i for i, call in enumerate(self.calls) if call[:2] == [service, command]]

    def only(self, service, command):
        found = self.find(service, command)
        assert len(found) == 1, f"expected one {service} {command}, got {len(found)}"
        return self.calls[found[0]]

    def changes(self):
        result = []
        for call in self.calls:
            if call[0] == "step":
                result.append(call)
            elif call[0] == "terraform":
                if len(call) > 1 and call[1] not in ("output", "validate", "plan", "state"):
                    result.append(call)
            elif len(call) > 1 and call[1].startswith(("describe-", "get-", "list-", "head-")):
                continue
            else:
                result.append(call)
        return result


def option(call, name):
    return call[call.index(name) + 1] if name in call else None


def deleted_parameters(r):
    names = {option(r.calls[i], "--name") for i in r.find("ssm", "delete-parameter")}
    for i in r.find("ssm", "delete-parameters"):
        call = r.calls[i]
        names.update(n for n in call[call.index("--names") + 1 :] if not n.startswith("-"))
    return names


class Sandbox:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.root = tmp_path / "repo"
        self.infra = self.root / "infra"
        self.terraform = self.infra / "terraform"
        self.terraform.mkdir(parents=True)
        self.bindir = tmp_path / "bin"
        self.bindir.mkdir()
        self.world_path = tmp_path / "world.json"
        self.log = tmp_path / "calls.log"
        self.world = {}
        self.extra_env = {}

        for name in ("setup.sh", "teardown.sh", "aws_env.sh", "stop_work.sh"):
            source = INFRA / name
            if source.exists():
                shutil.copy2(source, self.infra / name)

        for name in (
            "build_web_lambda.sh",
            "build_ffprobe_layer.sh",
            "build_report_layer.sh",
            "build_image.sh",
            "deploy_web.sh",
        ):
            self.write_stub(self.infra / name, STEP_SH.replace("__NAME__", name[:-3]))
        self.write_stub(self.infra / "apply_schema.py", STEP_PY.replace("__NAME__", "apply_schema"))
        self.write_stub(
            self.infra / "upload_model_data.py",
            STEP_PY.replace("__NAME__", "upload_model_data"),
        )

        self.write_stub(self.bindir / "aws", AWS_STUB)
        self.write_stub(self.bindir / "terraform", TERRAFORM_STUB)
        self.write_stub(self.bindir / "git", GIT_STUB)
        self.write_stub(self.bindir / "sleep", SLEEP_STUB)

    @staticmethod
    def write_stub(path, text):
        path.write_text(text)
        path.chmod(0o755)

    def seed(
        self,
        *,
        tfvars=True,
        hf_token=True,
        backend=False,
        worker_image="sha256:" + "a" * 64,
        artifacts=False,
    ):
        if tfvars:
            (self.terraform / "terraform.tfvars").write_text(
                f'bucket_name = "{DATA_BUCKET}"\n'
                'region = "us-east-1"\n'
                f'worker_image = "{worker_image}"\n'
            )
        if hf_token:
            (self.root / ".env").write_text("HF_TOKEN=fake-token\n")
            self.extra_env["HF_TOKEN"] = "fake-token"
        if backend:
            (self.terraform / "backend.hcl").write_text(f'bucket = "{STATE_BUCKET}"\n')
        if artifacts:
            for rel in (
                "build/web_lambda.zip",
                "infra/terraform/.build/ffprobe_layer.zip",
                "infra/terraform/.build/report_layer.zip",
            ):
                path = self.root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fake")

    def run(self, script, *args, world=None, input_text=None, tty=False):
        if world is not None:
            self.world = world
        self.world_path.write_text(json.dumps(self.world))
        self.log.unlink(missing_ok=True)
        env = {
            "PATH": f"{self.bindir}:{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "FAKE_WORLD": str(self.world_path),
            "FAKE_LOG": str(self.log),
            "FAKE_REPO": str(self.root),
            "AWS_ACCESS_KEY_ID": "x",
            "AWS_SECRET_ACCESS_KEY": "x",
            "AWS_PROFILE": "neurolens",
            "NEUROLENS_AWS_PROFILE": "neurolens",
        }
        env.update(self.extra_env)
        script_path = self.infra / script
        assert script_path.exists(), f"{script_path} does not exist yet"
        command = ["bash", str(script_path), *args]

        if tty:
            master, slave = pty.openpty()
            proc = subprocess.Popen(
                command,
                cwd=self.root,
                env=env,
                stdin=slave,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            os.close(slave)
            if input_text is not None:
                os.write(master, (input_text + "\n").encode())
            try:
                stdout, stderr = proc.communicate(timeout=60)
            finally:
                os.close(master)
            code = proc.returncode
        else:
            proc = subprocess.run(
                command,
                cwd=self.root,
                env=env,
                input=input_text if input_text is not None else "",
                capture_output=True,
                text=True,
                timeout=60,
            )
            stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode

        result = Run(code, stdout, stderr, self.log)
        result.world = json.loads(self.world_path.read_text())
        self.world = result.world
        return result


@pytest.fixture
def sandbox(tmp_path):
    return Sandbox(tmp_path)


# ---------------------------------------------------------------- teardown.sh


@pytest.mark.parametrize(
    "input_text,tty",
    [("999999999999", True), ("", False), (None, False)],
)
def test_teardown_wrong_or_empty_confirmation_changes_nothing(sandbox, input_text, tty):
    r = sandbox.run("teardown.sh", world=teardown_world(), input_text=input_text, tty=tty)
    assert r.code != 0
    assert r.changes() == []
    assert r.find("terraform", "destroy") == []


def test_teardown_refuses_when_caller_and_terraform_accounts_differ(sandbox):
    r = sandbox.run(
        "teardown.sh",
        world=teardown_world(terraform_account="222222222222"),
        input_text=ACCOUNT,
        tty=True,
    )
    assert r.code != 0
    assert ACCOUNT in r.text or "mismatch" in r.text.lower()
    assert r.find("terraform", "destroy") == []


def test_teardown_refuses_when_terraform_has_no_state(sandbox):
    r = sandbox.run(
        "teardown.sh",
        world=teardown_world(terraform_state=False),
        input_text=ACCOUNT,
        tty=True,
    )
    assert r.code != 0
    assert r.changes() == []
    assert r.find("terraform", "destroy") == []


def test_teardown_refuses_when_plan_shows_the_state_is_behind_the_code(sandbox):
    r = sandbox.run(
        "teardown.sh",
        world=teardown_world(plan_exit=2),
        input_text=ACCOUNT,
        tty=True,
    )
    assert r.code != 0
    assert r.find("terraform", "plan")
    assert "terraform -chdir=infra/terraform apply" in r.text
    assert "teardown.sh" in r.text
    assert r.find("terraform", "destroy") == []
    assert deleted_parameters(r) == set()
    assert r.find("sagemaker", "delete-action") == []
    assert r.find("sagemaker", "delete-context") == []


def test_teardown_refuses_when_plan_fails(sandbox):
    r = sandbox.run(
        "teardown.sh",
        world=teardown_world(plan_exit=1),
        input_text=ACCOUNT,
        tty=True,
    )
    assert r.code != 0
    assert r.find("terraform", "plan")
    assert "cannot plan" in r.text.lower()
    assert r.find("terraform", "destroy") == []
    assert deleted_parameters(r) == set()


def test_teardown_proceeds_when_plan_shows_no_changes(sandbox):
    r = sandbox.run(
        "teardown.sh",
        world=teardown_world(plan_exit=0),
        input_text=ACCOUNT,
        tty=True,
    )
    assert r.code == 0, r.err
    assert r.find("terraform", "plan")
    assert r.find("terraform", "destroy")
    assert "TEARDOWN COMPLETE" in r.out


@pytest.mark.parametrize("resume", [False, True])
def test_teardown_refuses_when_a_leftover_read_is_denied(sandbox, resume):
    if resume:
        sandbox.seed()
    world = teardown_world(
        terraform_state_empty=resume,
        denied=["resourcegroupstaggingapi get-resources"],
    )
    r = sandbox.run("teardown.sh", world=world, input_text=ACCOUNT, tty=True)
    assert r.code != 0
    assert "Type the AWS account ID" not in r.text
    assert "Nothing was deleted" in r.text
    assert "ACCOUNT_OWNER.md" in r.text
    assert "tag" in r.text.lower()
    assert r.find("terraform", "destroy") == []
    assert deleted_parameters(r) == set()
    assert r.find("sagemaker", "delete-action") == []
    assert r.find("sagemaker", "delete-context") == []


def test_teardown_no_destroy_when_stop_is_not_proven(sandbox):
    world = teardown_world(instances=1, status="Failed")
    r = sandbox.run("teardown.sh", world=world, input_text=ACCOUNT, tty=True)
    assert r.code != 0
    assert "ALL STOPPED" not in r.out
    assert r.find("terraform", "destroy") == []


def test_teardown_no_ssm_delete_when_destroy_fails(sandbox):
    world = teardown_world(fail=["terraform destroy"])
    r = sandbox.run("teardown.sh", world=world, input_text=ACCOUNT, tty=True)
    assert r.code != 0
    assert r.find("terraform", "destroy")
    assert r.find("ssm", "delete-parameter") == []


def test_teardown_deletes_lineage_associations_then_actions_then_contexts(sandbox):
    action = f"arn:aws:sagemaker:us-east-1:{ACCOUNT}:action/neurolens-1-1-aws-endpoint"
    context = f"arn:aws:sagemaker:us-east-1:{ACCOUNT}:context/neurolens-1-aws-endpoint"
    world = teardown_world(
        tagged=[action, context],
        associations=[{"source": action, "destination": context}],
    )
    r = sandbox.run("teardown.sh", world=world, input_text=ACCOUNT, tty=True)
    assert r.code == 0, r.err
    assert r.world["associations"] == []
    assert r.world["tagged"] == []
    associations = r.find("sagemaker", "delete-association")
    actions = r.find("sagemaker", "delete-action")
    contexts = r.find("sagemaker", "delete-context")
    assert associations and actions and contexts
    assert max(associations) < min(actions) < min(contexts)
    assert "TEARDOWN COMPLETE" in r.out


def test_teardown_is_incomplete_when_a_lineage_record_cannot_be_deleted(sandbox):
    action = f"arn:aws:sagemaker:us-east-1:{ACCOUNT}:action/neurolens-2-1-aws-endpoint"
    context = f"arn:aws:sagemaker:us-east-1:{ACCOUNT}:context/neurolens-2-aws-endpoint"
    world = teardown_world(
        tagged=[action, context],
        associations=[{"source": action, "destination": context}],
        lineage_fail=["neurolens-2-1-aws-endpoint"],
    )
    r = sandbox.run("teardown.sh", world=world, input_text=ACCOUNT, tty=True)
    assert r.code != 0
    assert "TEARDOWN COMPLETE" not in r.out
    assert "INCOMPLETE" in r.text
    assert "neurolens-2-1-aws-endpoint" in r.text
    assert r.world["associations"] == []


def test_teardown_runs_stop_destroy_secrets_then_leftover_checks(sandbox):
    r = sandbox.run("teardown.sh", world=teardown_world(), input_text=ACCOUNT, tty=True)
    assert r.code == 0, r.err

    stop = max(
        r.find("application-autoscaling", "describe-scalable-targets")
        + r.find("sagemaker", "describe-endpoint")
    )
    destroy = r.find("terraform", "destroy")[0]
    deletes = r.find("ssm", "delete-parameter") + r.find("ssm", "delete-parameters")
    # Reads also run as a probe before confirmation; use the last call of each as the real check.
    leftovers = [
        r.find("resourcegroupstaggingapi", "get-resources")[-1],
        r.find("secretsmanager", "list-secrets")[-1],
        r.find("sagemaker", "list-endpoint-configs")[-1],
        r.find("sagemaker", "list-models")[-1],
        r.find("ssm", "get-parameters-by-path")[-1],
        r.find("iam", "list-roles")[-1],
    ]
    assert stop < destroy < min(deletes) < min(leftovers)

    assert deleted_parameters(r) == set(SSM_SECRETS)
    assert "TEARDOWN COMPLETE" in r.out


def test_teardown_clean_run_prints_complete(sandbox):
    r = sandbox.run("teardown.sh", world=teardown_world(), input_text=ACCOUNT, tty=True)
    assert r.code == 0, r.err
    assert "TEARDOWN COMPLETE" in r.out
    assert r.find("s3", "sync") == []


def test_teardown_skips_the_stop_when_the_endpoint_does_not_exist(sandbox):
    r = sandbox.run(
        "teardown.sh",
        world=teardown_world(endpoint_missing=True),
        input_text=ACCOUNT,
        tty=True,
    )
    assert r.code == 0, r.err
    assert r.find("sagemaker", "describe-endpoint")
    assert r.find("application-autoscaling", "register-scalable-target") == []
    assert r.find("application-autoscaling", "describe-scalable-targets") == []
    assert r.find("terraform", "destroy")
    assert "TEARDOWN COMPLETE" in r.out


@pytest.mark.parametrize(
    "overrides,needle",
    [
        ({"tagged": ["arn:aws:s3:::neurolens-leftover"]}, "neurolens-leftover"),
        ({"secrets": ["rds!cluster-deadbeef"]}, "rds!cluster-deadbeef"),
        ({"endpoint_configs": ["neurolens-deadbeef"]}, "neurolens-deadbeef"),
        ({"models": ["neurolens-deadbeef"]}, "neurolens-deadbeef"),
        ({"ssm_parameters": [*SSM_SECRETS, "/neurolens/leftover"]}, "/neurolens/leftover"),
        ({"iam_roles": ["neurolens-leftover"]}, "neurolens-leftover"),
    ],
)
def test_teardown_reports_a_leftover_and_is_not_complete(sandbox, overrides, needle):
    r = sandbox.run("teardown.sh", world=teardown_world(**overrides), input_text=ACCOUNT, tty=True)
    assert r.code != 0
    assert "TEARDOWN COMPLETE" not in r.out
    assert needle in r.text


def test_teardown_tagged_leftover_says_the_tag_list_lags_and_to_rerun_later(sandbox):
    world = teardown_world(
        tagged=[f"arn:aws:cognito-idp:us-east-1:{ACCOUNT}:userpool/us-east-1_FAKE"]
    )
    r = sandbox.run("teardown.sh", world=world, input_text=ACCOUNT, tty=True)
    assert r.code != 0
    assert "TEARDOWN COMPLETE" not in r.out
    assert "lag" in r.text.lower()
    assert "minutes" in r.text.lower()
    assert "infra/teardown.sh" in r.text


def test_teardown_ignores_unrelated_secrets_models_and_configs(sandbox):
    world = teardown_world(
        secrets=["other-app"],
        endpoint_configs=["other-config"],
        models=["other-model"],
    )
    r = sandbox.run("teardown.sh", world=world, input_text=ACCOUNT, tty=True)
    assert r.code == 0, r.err
    assert "TEARDOWN COMPLETE" in r.out


def test_teardown_rerun_after_destroy_resumes_at_secrets_and_check(sandbox):
    sandbox.seed()
    first = sandbox.run("teardown.sh", world=teardown_world(), input_text=ACCOUNT, tty=True)
    assert first.code == 0, first.err
    assert first.world["terraform_state_empty"] is True

    second = sandbox.run("teardown.sh", world=first.world, input_text=ACCOUNT, tty=True)
    assert second.code == 0, second.err
    assert second.find("application-autoscaling", "describe-scalable-targets") == []
    assert second.find("sagemaker", "describe-endpoint") == []
    assert second.find("s3", "cp") == []
    assert second.find("s3", "sync") == []
    assert second.find("terraform", "destroy") == []
    assert deleted_parameters(second) == set(SSM_SECRETS)
    assert second.find("ssm", "get-parameters-by-path")
    assert "TEARDOWN COMPLETE" in second.out


def test_teardown_empty_state_wrong_confirmation_changes_nothing(sandbox):
    sandbox.seed()
    r = sandbox.run(
        "teardown.sh",
        world=teardown_world(terraform_state_empty=True),
        input_text="999999999999",
        tty=True,
    )
    assert r.code != 0
    assert "Type the AWS account ID" in r.text
    assert r.changes() == []
    assert r.find("ssm", "delete-parameter") == []


def test_teardown_refuses_when_terraform_state_cannot_be_read(sandbox):
    r = sandbox.run(
        "teardown.sh",
        world=teardown_world(fail=["terraform state"]),
        input_text=ACCOUNT,
        tty=True,
    )
    assert r.code != 0
    assert "state" in r.text.lower()
    assert r.changes() == []
    assert r.find("terraform", "destroy") == []


def test_teardown_never_deletes_the_state_bucket_or_the_deploy_user(sandbox):
    r = sandbox.run("teardown.sh", world=teardown_world(), input_text=ACCOUNT, tty=True)
    assert r.code == 0, r.err
    assert "tfstate" not in [
        option(c, "--bucket") for c in r.calls if c[:2] == ["s3api", "delete-bucket"]
    ]
    assert r.find("iam", "delete-user") == []
    assert r.find("iam", "delete-policy") == []


def test_terraform_empties_the_data_bucket_on_destroy():
    main = (INFRA / "terraform" / "main.tf").read_text()
    bucket = re.search(r'resource\s+"aws_s3_bucket"\s+"main"\s*\{.*?\n\}', main, re.S)
    assert bucket, "the data bucket resource is missing"
    assert re.search(r"force_destroy\s*=\s*true", bucket.group(0))


def test_terraform_empties_the_worker_repository_on_destroy():
    sagemaker = (INFRA / "terraform" / "sagemaker.tf").read_text()
    repository = re.search(
        r'resource\s+"aws_ecr_repository"\s+"worker"\s*\{.*?\n\}', sagemaker, re.S
    )
    assert repository, "the worker ECR repository resource is missing"
    assert re.search(r"force_delete\s*=\s*true", repository.group(0))


def test_terraform_exposes_the_account_id():
    outputs = (INFRA / "terraform" / "outputs.tf").read_text()
    account = re.search(r'output\s+"account_id"\s*\{.*?\n\}', outputs, re.S)
    assert account, "outputs.tf has no account_id output"
    assert "account_id" in account.group(0)


def test_deploy_policies_allow_every_teardown_leftover_read():
    documents = [
        json.loads(path.read_text())
        for path in sorted((INFRA / "iam").glob("neurolens-deploy-*.json"))
    ]
    granted = {}
    for document in documents:
        for statement in document["Statement"]:
            actions = statement.get("Action", [])
            actions = [actions] if isinstance(actions, str) else actions
            for action in actions:
                resources = statement.get("Resource")
                if isinstance(resources, str):
                    resources = [resources]
                granted.setdefault(action, []).extend(resources)
    for action in (
        "tag:GetResources",
        "secretsmanager:ListSecrets",
        "sagemaker:ListEndpointConfigs",
        "sagemaker:ListModels",
        "iam:ListRoles",
    ):
        assert "*" in granted.get(action, []), action
    # SSM may check the path itself (/neurolens) or a name under it, so both are granted.
    for resource in (
        "arn:aws:ssm:us-east-1:*:parameter/neurolens",
        "arn:aws:ssm:us-east-1:*:parameter/neurolens/*",
    ):
        assert resource in granted.get("ssm:GetParametersByPath", []), resource


def test_deploy_compute_policy_covers_teardown_tags_and_lineage_deletes():
    path = INFRA / "iam" / "neurolens-deploy-compute.json"
    text = path.read_text()
    document = json.loads(text)
    granted = {}
    for statement in document["Statement"]:
        actions = statement.get("Action", [])
        actions = [actions] if isinstance(actions, str) else actions
        resources = statement.get("Resource", [])
        resources = [resources] if isinstance(resources, str) else resources
        for action in actions:
            granted.setdefault(action, []).extend(resources)

    assert "*" in granted.get("tag:GetResources", [])
    assert "*" in granted.get("sagemaker:ListAssociations", [])
    narrow = {
        "arn:aws:sagemaker:*:*:action/neurolens-*",
        "arn:aws:sagemaker:*:*:context/neurolens-*",
    }
    for action in ("sagemaker:DeleteAction", "sagemaker:DeleteContext"):
        assert narrow <= set(granted.get(action, [])), action
    association = set(granted.get("sagemaker:DeleteAssociation", []))
    assert association, "sagemaker:DeleteAssociation is not granted"
    assert association <= narrow | {"*"}, association
    assert len("".join(text.split())) <= 6144


# ---------------------------------------------------------------- setup.sh


def test_setup_lists_all_missing_prerequisites_before_changing_anything(sandbox):
    r = sandbox.run(
        "setup.sh",
        world=setup_world(ssm_parameters=[], buckets=[]),
    )
    assert r.code != 0
    lowered = r.text.lower()
    for missing in ("google_client_secret", "flask_secret_key", "youtube_api_key"):
        assert missing in lowered, missing
    assert "terraform.tfvars" in lowered
    assert any(word in lowered for word in ("hf_token", "huggingface", "hugging face"))
    assert r.changes() == []
    assert not (sandbox.terraform / "backend.hcl").exists()
    assert not (sandbox.root / "build" / "web_lambda.zip").exists()


def test_setup_does_not_recreate_an_existing_state_bucket(sandbox):
    sandbox.seed(artifacts=True)
    r = sandbox.run("setup.sh", world=setup_world())
    assert r.code == 0, r.err
    assert r.find("s3api", "create-bucket") == []


def test_setup_builds_applies_schema_deploys_then_builds_the_image(sandbox):
    sandbox.seed(worker_image="")
    r = sandbox.run("setup.sh", world=setup_world())
    assert r.code == 0, r.err

    build_web = r.find("step", "build_web_lambda")[0]
    build_ffprobe = r.find("step", "build_ffprobe_layer")[0]
    build_report = r.find("step", "build_report_layer")[0]
    init = r.find("terraform", "init")[0]
    applies = r.find("terraform", "apply")
    schema = r.find("step", "apply_schema")[0]
    deploy = r.find("step", "deploy_web")[0]
    image = r.find("step", "build_image")[0]

    assert build_web < build_ffprobe < build_report < init < applies[0]
    assert applies[0] < schema < deploy < image < applies[1]


def test_setup_stops_at_the_first_failure_and_names_the_step(sandbox):
    sandbox.seed(worker_image="")
    r = sandbox.run("setup.sh", world=setup_world(fail=["step build_ffprobe_layer"]))
    assert r.code != 0
    assert "ffprobe" in r.text.lower()
    assert r.find("step", "build_report_layer") == []
    assert r.find("terraform", "init") == []
    assert r.find("step", "apply_schema") == []
    assert r.find("step", "deploy_web") == []


def test_setup_second_run_with_everything_present_creates_nothing(sandbox):
    sandbox.seed(worker_image="")
    first = sandbox.run("setup.sh", world=setup_world(buckets=[]))
    assert first.code == 0, first.err
    assert first.find("s3api", "create-bucket")
    assert first.find("step", "build_web_lambda")
    assert first.find("step", "build_image")

    second = sandbox.run("setup.sh")
    assert second.code == 0, second.err
    assert second.find("s3api", "create-bucket") == []
    for step in (
        "build_web_lambda",
        "build_ffprobe_layer",
        "build_report_layer",
        "build_image",
    ):
        assert second.find("step", step) == [], step


def test_setup_uploads_empty_model_data_before_the_endpoint_apply(sandbox):
    sandbox.seed(worker_image="")
    r = sandbox.run("setup.sh", world=setup_world(objects=[]))
    assert r.code == 0, r.err
    upload = r.find("step", "upload_model_data")
    assert upload, "model-data/ was empty but the upload script did not run"
    applies = r.find("terraform", "apply")
    assert len(applies) == 2
    assert upload[0] < applies[1]


def test_setup_skips_the_model_data_upload_when_the_prefix_is_not_empty(sandbox):
    sandbox.seed(worker_image="")
    r = sandbox.run("setup.sh", world=setup_world())
    assert r.code == 0, r.err
    assert r.find("step", "upload_model_data") == []
    assert len(r.find("terraform", "apply")) == 2


def test_setup_never_applies_the_endpoint_when_the_model_data_upload_fails(sandbox):
    sandbox.seed(worker_image="")
    r = sandbox.run(
        "setup.sh",
        world=setup_world(objects=[], fail=["step upload_model_data"]),
    )
    assert r.code != 0
    assert "model" in r.text.lower()
    assert len(r.find("terraform", "apply")) == 1
    assert r.find("step", "build_image") == []


def test_setup_refuses_the_endpoint_apply_when_model_data_is_still_empty(sandbox):
    sandbox.seed(worker_image="")
    r = sandbox.run("setup.sh", world=setup_world(objects=[], upload_leaves_empty=True))
    assert r.code != 0
    assert "model-data" in r.text.lower()
    assert len(r.find("terraform", "apply")) == 1
    assert r.find("step", "build_image") == []


@pytest.mark.parametrize("buckets", [[STATE_BUCKET], [STATE_BUCKET, DATA_BUCKET]])
def test_setup_refuses_the_first_apply_when_worker_image_is_set_but_model_data_is_empty(
    sandbox, buckets
):
    sandbox.seed(worker_image="sha256:" + "b" * 64)
    r = sandbox.run("setup.sh", world=setup_world(buckets=buckets, objects=[]))
    assert r.code != 0
    assert "worker_image" in r.text
    assert "model-data" in r.text.lower()
    assert r.find("terraform", "apply") == []
    assert r.find("step", "build_image") == []


def test_setup_allows_the_first_apply_when_worker_image_is_set_and_model_data_exists(sandbox):
    sandbox.seed(worker_image="sha256:" + "b" * 64)
    r = sandbox.run("setup.sh", world=setup_world())
    assert r.code == 0, r.err
    assert len(r.find("terraform", "apply")) == 1
    assert r.find("step", "build_image") == []


def test_setup_stores_a_missing_cognito_client_secret_before_deploying(sandbox):
    sandbox.seed()
    secrets = [s for s in SSM_SECRETS if s != "/neurolens/web/cognito_client_secret"]
    r = sandbox.run("setup.sh", world=setup_world(ssm_parameters=secrets))
    assert r.code == 0, r.err

    puts = [
        i
        for i in r.find("ssm", "put-parameter")
        if option(r.calls[i], "--name") == "/neurolens/web/cognito_client_secret"
    ]
    assert len(puts) == 1
    call = r.calls[puts[0]]
    assert option(call, "--type") == "SecureString"
    assert option(call, "--value") == "fake-cognito-secret"
    assert puts[0] < r.find("step", "deploy_web")[0]
    assert "/neurolens/web/cognito_client_secret" in r.world["ssm_parameters"]


def test_setup_does_not_overwrite_an_existing_cognito_client_secret(sandbox):
    sandbox.seed()
    r = sandbox.run("setup.sh", world=setup_world())
    assert r.code == 0, r.err
    names = [option(r.calls[i], "--name") for i in r.find("ssm", "put-parameter")]
    assert "/neurolens/web/cognito_client_secret" not in names
    assert r.find("step", "deploy_web")


# ---------------------------------------------------------------- model data layout


def load_upload_model_data():
    path = INFRA / "upload_model_data.py"
    spec = importlib.util.spec_from_file_location("upload_model_data", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upload_model_data_prepares_the_full_offline_layout(tmp_path, monkeypatch):
    module = load_upload_model_data()
    models = tmp_path / "models"
    data = tmp_path / "data"
    hub_cache = models / "hub"
    hub_cache.mkdir(parents=True)
    data.mkdir()
    downloaded = []

    def fake_snapshot_download(repo_id, cache_dir, token, allow_patterns):
        root = Path(cache_dir) / f"models--{repo_id.replace('/', '--')}"
        snapshot = root / "snapshots" / "rev"
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").write_text("{}")
        (root / "blobs").mkdir()
        (root / "blobs" / "duplicate").write_text("blob")
        return str(snapshot)

    def fake_download_file(url, target, expected_size):
        downloaded.append((url, target.relative_to(tmp_path).as_posix(), expected_size))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fake")

    monkeypatch.setattr(module, "download_huggingface", fake_snapshot_download)
    monkeypatch.setattr(module, "download_file", fake_download_file)
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(b"map"))
    monkeypatch.setattr(module, "verify_sha256", lambda path, expected: None)

    module.prepare_model_data(
        {"model": {"repo_id": "facebook/tribev2"}}, "token", hub_cache, models, data
    )
    plan = module.upload_plan(hub_cache, models, data)
    files = {key.removeprefix(module.PREFIX) for key in plan}
    assert all(path.is_relative_to(tmp_path) and path.is_file() for path in plan.values())

    hub = "models/hub/models--"
    assert files == {
        f"{hub}facebook--tribev2/snapshots/rev/config.json",
        f"{hub}facebook--vjepa2-vitg-fpc64-256/snapshots/rev/config.json",
        f"{hub}facebook--w2v-bert-2.0/snapshots/rev/config.json",
        f"{hub}meta-llama--Llama-3.2-3B/snapshots/rev/config.json",
        f"{hub}Systran--faster-whisper-large-v3/snapshots/rev/config.json",
        "models/torch/hub/checkpoints/wav2vec2_fairseq_large_lv60k_asr_ls960.pth",
        "data/hcp_mmp/lh.HCPMMP1.annot",
        "data/hcp_mmp/rh.HCPMMP1.annot",
        "data/nilearn/destrieux_surface/left.aparc.a2009s.annot",
        "data/nilearn/destrieux_surface/right.aparc.a2009s.annot",
        "data/nilearn/README.md",
    }

    destrieux_urls = {url for url, _ in module.DESTRIEUX_FILES.values()}
    assert {url for url, _, _ in downloaded} == {module.ALIGNMENT_WEIGHTS[1]} | destrieux_urls
