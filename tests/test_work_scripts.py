"""infra/start_work.sh and infra/stop_work.sh against a fake `aws` (docs/M3e_spec.md §6b).

The scripts run under bash with stub `aws`, `terraform` and `sleep` first on PATH. The `aws` stub
keeps the world in a JSON file (so a change is seen by later calls), logs every call, and fails
loudly (exit 99) on any call it does not know, so nothing slips through unrecorded.

start_work.sh sets the SageMaker variant's maximum to 1; --keep-warm (--hours N) also sets its
minimum to 1 and records a one-time scheduled action that puts the minimum back; --keep-db is
refused. stop_work.sh deletes keep-warm actions first, sets the scalable target's min and max to 0,
reads them back, sets the endpoint's desired count to 0, waits for DescribeEndpoint to show 0 and
prints ALL STOPPED. Uploads are refused while paused by the web app (tests/test_m3e_web.py).
"""

import datetime
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INFRA = REPO / "infra"

AWS_STUB = r"""#!/usr/bin/env python3
import json, os, re, sys
args = sys.argv[1:]
world_path = os.environ["FAKE_WORLD"]
w = json.load(open(world_path))
open(os.environ["FAKE_LOG"], "a").write(json.dumps(args) + "\n")
key = " ".join(args[:2])


def opt(name, default=None):
    return args[args.index(name) + 1] if name in args else default


def out(text=""):
    print(text)
    json.dump(w, open(world_path, "w"))
    sys.exit(0)


if key in w.get("fail", []):
    print("fake aws: injected failure for " + key, file=sys.stderr)
    sys.exit(255)
query = opt("--query", "")
target = w["target"]


if key == "application-autoscaling register-scalable-target":
    if opt("--min-capacity") is not None:
        target["min"] = int(opt("--min-capacity"))
    if opt("--max-capacity") is not None:
        target["max"] = int(opt("--max-capacity"))
    out()
if key == "application-autoscaling describe-scalable-targets":
    if "length(" in query:
        out("1")
    shown = w.get("readback", target)
    out(f'{shown["min"]}\t{shown["max"]}')
if key == "application-autoscaling put-scheduled-action":
    known = {
        "--service-namespace", "--scheduled-action-name", "--resource-id",
        "--scalable-dimension", "--schedule", "--scalable-target-action",
        "--timezone", "--start-time", "--end-time", "--region", "--profile",
        "--output", "--query", "--no-cli-pager", "--cli-input-json",
    }
    unknown = [a for a in args[2:] if a.startswith("--") and a not in known]
    if unknown:
        print("Unknown options: " + ", ".join(unknown), file=sys.stderr)
        sys.exit(252)
    schedule = opt("--schedule")
    if not schedule or not re.fullmatch(r"at\(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\)", schedule):
        print(
            "An error occurred (ValidationException) when calling the "
            "PutScheduledAction operation: Invalid schedule: " + repr(schedule),
            file=sys.stderr,
        )
        sys.exit(254)
    w["scheduled"].append(
        {
            "name": opt("--scheduled-action-name"),
            "at": schedule,
            "action": opt("--scalable-target-action", ""),
        }
    )
    out()
if key == "application-autoscaling delete-scheduled-action":
    name = opt("--scheduled-action-name")
    if name not in [a["name"] for a in w["scheduled"]]:
        print(
            "An error occurred (ObjectNotFoundException) when calling the "
            "DeleteScheduledAction operation: No scheduled action found",
            file=sys.stderr,
        )
        sys.exit(254)
    w["scheduled"] = [a for a in w["scheduled"] if a["name"] != name]
    out()
if key == "application-autoscaling describe-scheduled-actions":
    out("\t".join(a["name"] for a in w["scheduled"]))
if key == "sagemaker update-endpoint-weights-and-capacities":
    if w.get("updating_for", 0) > 0 or w.get("status") in (
        "Creating",
        "Updating",
        "SystemUpdating",
    ):
        print(
            "An error occurred (ValidationException) when calling the "
            "UpdateEndpointWeightsAndCapacities operation: Cannot update "
            'in-progress endpoint "neurolens".',
            file=sys.stderr,
        )
        sys.exit(254)
    if w.get("refuse_update_once"):
        w["refuse_update_once"] = False
        json.dump(w, open(world_path, "w"))
        print(
            "An error occurred (ValidationException) when calling the "
            "UpdateEndpointWeightsAndCapacities operation: Cannot update "
            'in-progress endpoint "neurolens".',
            file=sys.stderr,
        )
        sys.exit(254)
    desired = opt("--desired-weights-and-capacities", "")
    match = re.search(r"DesiredInstanceCount=(\d+)", desired)
    if match:
        w["desired"] = int(match.group(1))
        if w["desired"] == 0 and not w.get("stuck"):
            w["instances"] = 0
    out()
if key == "sagemaker describe-endpoint":
    if "endpointstatus" in query.lower():
        if w.get("updating_for", 0) > 0:
            w["updating_for"] -= 1
            if w["updating_for"] == 0 and "settles_to" in w:
                w["instances"] = w["settles_to"]
                w["desired"] = w["settles_to"]
            status = "Updating"
        else:
            status = w.get("status", "InService")
        out(f"{status}\t{w.get('desired', w['instances'])}\t{w['instances']}")
    elif query == "ProductionVariants[0].CurrentInstanceCount":
        out(str(w["instances"]))
print("fake aws: unexpected call: " + " ".join(args), file=sys.stderr)
sys.exit(99)
"""

SH = "#!/usr/bin/env bash\n"


def healthy_world(**overrides):
    world = {
        "target": {"min": 0, "max": 0},
        "instances": 0,
        "scheduled": [],
        "fail": [],
    }
    world.update(overrides)
    return world


class Run:
    def __init__(self, proc, log):
        self.proc = proc
        self.out = proc.stdout
        self.err = proc.stderr
        self.code = proc.returncode
        self.calls = (
            [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        )

    def find(self, service, command):
        return [i for i, c in enumerate(self.calls) if c[:2] == [service, command]]

    def only(self, service, command):
        found = self.find(service, command)
        assert len(found) == 1, f"expected one {service} {command}, got {len(found)}"
        return self.calls[found[0]]

    def changes(self):
        """Calls that change something (everything not a read)."""
        return [c for c in self.calls if not c[1].startswith(("describe-", "get-"))]


def option(call, name):
    return call[call.index(name) + 1] if name in call else None


@pytest.fixture
def sandbox(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "aws").write_text(AWS_STUB)
    (bindir / "terraform").write_text(SH + "echo us-east-1\n")
    (bindir / "sleep").write_text(SH + 'echo sleep >> "$FAKE_LOG.sleeps"\n')
    for f in bindir.iterdir():
        f.chmod(0o755)

    def run(script, world, *args):
        world_file = tmp_path / "world.json"
        world_file.write_text(json.dumps(world))
        log = tmp_path / "calls.log"
        log.unlink(missing_ok=True)
        env = {
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "FAKE_WORLD": str(world_file),
            "FAKE_LOG": str(log),
            "AWS_ACCESS_KEY_ID": "x",
            "AWS_SECRET_ACCESS_KEY": "x",
        }
        proc = subprocess.run(
            ["bash", str(INFRA / script), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        result = Run(proc, log)
        result.world = json.loads(world_file.read_text())
        result.sleeps = (
            len((tmp_path / "calls.log.sleeps").read_text().split())
            if (tmp_path / "calls.log.sleeps").exists()
            else 0
        )
        return result

    return run


# ---------------------------------------------------------------- start_work.sh


def test_start_plain_sets_the_maximum_to_one(sandbox):
    r = sandbox("start_work.sh", healthy_world())
    assert r.code == 0, r.err
    call = r.only("application-autoscaling", "register-scalable-target")
    assert option(call, "--resource-id") == "endpoint/neurolens/variant/main"
    assert option(call, "--max-capacity") == "1"
    assert option(call, "--min-capacity") in (None, "0")
    assert r.find("application-autoscaling", "put-scheduled-action") == []


@pytest.mark.parametrize(
    "args", [("start_work.sh",), ("start_work.sh", "--keep-warm"), ("stop_work.sh",)]
)
def test_the_work_scripts_never_touch_aurora(sandbox, args):
    r = sandbox(args[0], healthy_world(), *args[1:])
    assert r.code == 0, r.err
    assert [c for c in r.calls if c[0] == "rds"] == []


def test_keep_warm_sets_min_one_and_a_one_time_action(sandbox):
    before = datetime.datetime.now(datetime.timezone.utc)
    r = sandbox("start_work.sh", healthy_world(), "--keep-warm", "--hours", "2")
    assert r.code == 0, r.err
    target = r.only("application-autoscaling", "register-scalable-target")
    assert option(target, "--min-capacity") == "1"
    assert option(target, "--max-capacity") == "1"
    action = r.only("application-autoscaling", "put-scheduled-action")
    schedule = option(action, "--schedule")
    match = re.fullmatch(r"at\((\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\)", schedule or "")
    assert match, schedule
    ends = datetime.datetime.fromisoformat(match.group(1)).replace(tzinfo=datetime.timezone.utc)
    expected = before + datetime.timedelta(hours=2)
    assert abs((ends - expected).total_seconds()) <= 300, (schedule, before)
    assert "MinCapacity=0" in (option(action, "--scalable-target-action") or "")
    put = r.find("application-autoscaling", "put-scheduled-action")
    min_one = [
        i
        for i in r.find("application-autoscaling", "register-scalable-target")
        if option(r.calls[i], "--min-capacity") == "1"
    ]
    assert min_one and put[0] < min_one[0]


def test_keep_warm_failing_action_never_raises_the_minimum(sandbox):
    r = sandbox(
        "start_work.sh",
        healthy_world(fail=["application-autoscaling put-scheduled-action"]),
        "--keep-warm",
        "--hours",
        "2",
    )
    assert r.code != 0
    assert not [
        c
        for c in r.calls
        if c[:2] == ["application-autoscaling", "register-scalable-target"]
        and option(c, "--min-capacity") == "1"
    ]


def test_start_refuses_keep_db(sandbox):
    r = sandbox("start_work.sh", healthy_world(), "--keep-db")
    assert r.code == 2
    assert r.calls == []
    assert "usage" in r.err


@pytest.mark.parametrize(
    "args",
    [
        ("--hours", "2"),
        ("--keep-warm", "--hours", "0"),
        ("--keep-warm", "--hours", "5"),
        ("--max", "3"),
    ],
)
def test_start_rejects_bad_arguments_before_any_aws_call(sandbox, args):
    r = sandbox("start_work.sh", healthy_world(), *args)
    assert r.code == 2
    assert r.calls == []


# ---------------------------------------------------------------- stop_work.sh


def test_stop_sets_the_target_and_endpoint_to_zero_and_prints_all_stopped(sandbox):
    r = sandbox("stop_work.sh", healthy_world(instances=1))
    assert r.code == 0, r.err
    target = r.only("application-autoscaling", "register-scalable-target")
    assert option(target, "--resource-id") == "endpoint/neurolens/variant/main"
    assert option(target, "--min-capacity") == "0"
    assert option(target, "--max-capacity") == "0"
    endpoint = r.only("sagemaker", "update-endpoint-weights-and-capacities")
    assert option(endpoint, "--endpoint-name") == "neurolens"
    assert "DesiredInstanceCount=0" in (option(endpoint, "--desired-weights-and-capacities") or "")
    assert "ALL STOPPED" in r.out
    assert "NOT CONFIRMED" not in r.err


def test_stop_deletes_the_keep_warm_action_before_scaling_down(sandbox):
    world = healthy_world(
        scheduled=[{"name": "neurolens-keep-warm-end", "at": "2026-10-22T12:00:00Z", "action": ""}]
    )
    r = sandbox("stop_work.sh", world)
    assert r.code == 0, r.err
    deleted = r.find("application-autoscaling", "delete-scheduled-action")[0]
    registered = r.find("application-autoscaling", "register-scalable-target")[0]
    read_back = r.find("application-autoscaling", "describe-scalable-targets")[0]
    assert deleted < registered < read_back


def test_stop_fails_when_the_scalable_target_reads_back_nonzero(sandbox):
    r = sandbox("stop_work.sh", healthy_world(readback={"min": 1, "max": 0}))
    assert r.code != 0
    assert "ALL STOPPED" not in r.out
    assert "NOT CONFIRMED" in r.err
    assert "min=1 max=0" in r.err


def test_stop_fails_when_the_scalable_target_cannot_be_read_back(sandbox):
    r = sandbox(
        "stop_work.sh",
        healthy_world(fail=["application-autoscaling describe-scalable-targets"]),
    )
    assert r.code != 0
    assert "ALL STOPPED" not in r.out
    assert "NOT CONFIRMED" in r.err
    assert "could not read the scalable target back" in r.err


def test_stop_without_a_keep_warm_action_still_stops(sandbox):
    r = sandbox("stop_work.sh", healthy_world(instances=1))
    assert r.code == 0, r.err
    assert "ALL STOPPED" in r.out
    assert "NOT CONFIRMED" not in r.err
    assert "PROBLEM" not in r.err
    r.only("application-autoscaling", "delete-scheduled-action")


def test_stop_deletes_any_keep_warm_action(sandbox):
    world = healthy_world(
        scheduled=[{"name": "neurolens-keep-warm-end", "at": "2026-10-22T12:00:00Z", "action": ""}]
    )
    r = sandbox("stop_work.sh", world)
    assert r.code == 0, r.err
    deleted = r.only("application-autoscaling", "delete-scheduled-action")
    assert option(deleted, "--scheduled-action-name") == "neurolens-keep-warm-end"


def test_stop_endpoint_that_never_reaches_zero_is_not_confirmed(sandbox):
    r = sandbox("stop_work.sh", healthy_world(instances=1, stuck=True))
    assert r.code != 0
    assert "ALL STOPPED" not in r.out
    assert "NOT CONFIRMED" in r.err
    assert r.sleeps >= 1  # it waited for the instance


@pytest.mark.parametrize(
    "failing, overrides",
    [
        ("application-autoscaling register-scalable-target", {}),
        ("application-autoscaling delete-scheduled-action", {}),
        # The new script skips the update call when desired is already 0, so this case
        # needs an endpoint above 0 for the injected failure to be reached.
        ("sagemaker update-endpoint-weights-and-capacities", {"instances": 1}),
        ("sagemaker describe-endpoint", {}),
    ],
)
def test_stop_failing_call_is_never_all_stopped(sandbox, failing, overrides):
    r = sandbox("stop_work.sh", healthy_world(fail=[failing], **overrides))
    assert r.code != 0
    assert "ALL STOPPED" not in r.out
    assert "NOT CONFIRMED" in r.err


def test_stop_waits_for_an_updating_endpoint_that_settles_to_zero(sandbox):
    # Tonight's case: setting the target to 0/0 started an update that scales itself to 0.
    r = sandbox(
        "stop_work.sh",
        healthy_world(instances=1, desired=0, updating_for=3, settles_to=0),
    )
    assert r.code == 0, r.err
    assert "ALL STOPPED" in r.out
    assert "NOT CONFIRMED" not in r.err
    assert "PROBLEM" not in r.err
    assert r.sleeps >= 1
    assert "in-progress" not in r.err
    assert r.find("sagemaker", "update-endpoint-weights-and-capacities") == []


def test_stop_updates_only_after_an_updating_endpoint_becomes_inservice(sandbox):
    updating_for = 2
    r = sandbox("stop_work.sh", healthy_world(instances=1, updating_for=updating_for))
    assert r.code == 0, r.err
    assert "ALL STOPPED" in r.out
    updates = r.find("sagemaker", "update-endpoint-weights-and-capacities")
    assert len(updates) == 1
    describes = r.find("sagemaker", "describe-endpoint")
    assert len([d for d in describes if d < updates[0]]) >= updating_for + 1


def test_stop_endpoint_stuck_updating_is_not_confirmed(sandbox):
    r = sandbox("stop_work.sh", healthy_world(instances=1, updating_for=1000))
    assert r.code != 0
    assert "ALL STOPPED" not in r.out
    assert "NOT CONFIRMED" in r.err
    assert "Updating" in r.err
    assert r.sleeps <= 100


@pytest.mark.parametrize("status", ["Failed", "RollingBack", "OutOfService"])
def test_stop_endpoint_in_a_bad_status_is_not_confirmed(sandbox, status):
    r = sandbox("stop_work.sh", healthy_world(instances=1, status=status))
    assert r.code != 0
    assert "ALL STOPPED" not in r.out
    assert "NOT CONFIRMED" in r.err
    assert status in r.err
    assert r.find("sagemaker", "update-endpoint-weights-and-capacities") == []


def test_stop_retries_an_update_refused_as_in_progress(sandbox):
    r = sandbox("stop_work.sh", healthy_world(instances=1, refuse_update_once=True))
    assert r.code == 0, r.err
    assert "ALL STOPPED" in r.out
    assert "NOT CONFIRMED" not in r.err
    assert "PROBLEM" not in r.err
    assert len(r.find("sagemaker", "update-endpoint-weights-and-capacities")) >= 2
    assert r.sleeps >= 1


def test_stop_endpoint_already_at_zero_needs_no_update(sandbox):
    r = sandbox("stop_work.sh", healthy_world(instances=0, desired=0))
    assert r.code == 0, r.err
    assert "ALL STOPPED" in r.out
    assert "NOT CONFIRMED" not in r.err
    assert r.find("sagemaker", "update-endpoint-weights-and-capacities") == []
