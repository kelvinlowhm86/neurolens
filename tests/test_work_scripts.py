"""infra/start_work.sh and infra/stop_work.sh against a fake `aws` (no AWS is ever called).

The real scripts run under bash with stub `aws`, `terraform` and `sleep` first on PATH. The `aws`
stub keeps the world in a JSON file (so a changed group size is seen by later calls), logs every
call, and fails loudly (exit 99) on any call it does not know, so nothing can slip through
unrecorded. Tests assert on what money-relevant calls were made, and in what order.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INFRA = REPO / "infra"

AWS_STUB = r"""#!/usr/bin/env python3
import json, os, sys
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
asg = w["asg"]


def clear_workers():
    asg["instances"] = []
    w["machines"] = [m for m in w["machines"] if m["role"] != "worker"]


if key == "cloudwatch enable-alarm-actions":
    out()
if key == "cloudwatch describe-alarms":
    out(str(w["alarms_on"]) if "length(" in query else w["idle_state"])
if key == "events describe-rule":
    out(w["breaker_state"])
if key == "autoscaling describe-auto-scaling-groups":
    if asg is None:
        out("None")
    if "length(" in query:
        if w.get("drain", 0) > 0:
            w["drain"] -= 1
            if w["drain"] == 0:
                clear_workers()
        out(str(len(asg["instances"])))
    if "InstanceId" in query:
        out("\t".join(asg["instances"]) or "None")
    out(f'{asg["min"]}\t{asg["max"]}\t{asg["desired"]}')
if key == "autoscaling update-auto-scaling-group":
    for flag, field in (("--min-size", "min"), ("--max-size", "max"),
                        ("--desired-capacity", "desired")):
        if flag in args:
            asg[field] = int(opt(flag))
    if asg["desired"] == 0 and not w.get("stuck"):
        w["drain"] = w.get("drain_polls", 0)
        if w["drain"] == 0:
            clear_workers()
    out()
if key == "autoscaling set-instance-protection":
    out()
if key == "autoscaling put-scheduled-update-group-action":
    w["hold"] = True
    out()
if key == "autoscaling delete-scheduled-action":
    w["hold"] = False
    out()
if key == "autoscaling describe-scheduled-actions":
    out("neurolens-warm-hold-end" if w["hold"] else "")
if key == "rds describe-db-clusters":
    out(w["db_min"])
if key == "rds modify-db-cluster":
    cfg = opt("--serverless-v2-scaling-configuration")
    w["db_min"] = cfg.split("MinCapacity=")[1].split(",")[0]
    out()
if key == "ec2 describe-nat-gateways":
    states = next(a for a in args if a.startswith("Name=state,Values=")).split("=")[2].split(",")
    ids = [n["id"] for n in w["nats"] if n["state"] in states]
    out((ids[0] if ids else "None") if "[0]" in query else "\t".join(ids))
if key == "ec2 describe-route-tables":
    out(w.get("private_route_table", "rtb-1"))
if key == "ec2 describe-addresses":
    out("\n".join(f"{e}\tNone" for e in w["eips"]))
if key == "ec2 describe-instances":
    filters = " ".join(args)
    if "Values=stopped" in filters:
        out("")
    rows = [m for m in w["machines"] if m["state"] != "stopped"]
    if "Name=tag:Role,Values=worker" in filters:
        rows = [m for m in rows if m["role"] == "worker"]
    out("\n".join(f'{m["id"]}\t{m["type"]}\t{m["role"]}\t{m["state"]}' for m in rows))
if key == "sqs get-queue-url":
    out("https://fake/queue")
if key == "sqs get-queue-attributes":
    out(f'{w["dlq"][0]}\t{w["dlq"][1]}')
print("fake aws: unexpected call: " + " ".join(args), file=sys.stderr)
sys.exit(99)
"""

SH = "#!/usr/bin/env bash\n"


def healthy_world(**overrides):
    world = {
        "asg": {"min": 0, "max": 0, "desired": 0, "instances": []},
        "machines": [],
        "db_min": "0.0",
        "nats": [],
        "eips": [],
        "dlq": [0, 0],
        "alarms_on": 3,
        "idle_state": "OK",
        "breaker_state": "ENABLED",
        "hold": False,
        "fail": [],
    }
    world.update(overrides)
    return world


def started_world(**overrides):
    """What start_work.sh expects to find: group 0/0/0, a NAT Gateway available."""
    return healthy_world(nats=[{"id": "nat-1", "state": "available"}], **overrides)


def running_worker_world(**overrides):
    return healthy_world(
        asg={"min": 1, "max": 1, "desired": 1, "instances": ["i-w1"]},
        machines=[{"id": "i-w1", "type": "g6e.xlarge", "role": "worker", "state": "running"}],
        hold=True,
        **overrides,
    )


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

    def changes(self):
        """Calls that change something (everything not a read or an alarm re-enable)."""
        reads = ("describe-", "get-", "enable-alarm-actions")
        return [c for c in self.calls if not c[1].startswith(reads)]


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


def test_start_without_nat_gateway_changes_nothing(sandbox):
    r = sandbox("start_work.sh", healthy_world(), "--keep-worker-and-db")
    assert r.code != 0
    assert "NAT Gateway" in r.err
    assert r.changes() == []


def test_start_refuses_when_the_private_route_does_not_use_the_nat_gateway(sandbox):
    r = sandbox("start_work.sh", started_world(private_route_table="None"))
    assert r.code != 0
    assert "route" in r.err
    assert r.changes() == []


def test_start_failure_after_aurora_was_raised_says_to_run_stop_work(sandbox):
    world = started_world(fail=["autoscaling update-auto-scaling-group"])
    r = sandbox("start_work.sh", world, "--keep-worker-and-db")
    assert r.code != 0
    assert r.find("rds", "modify-db-cluster")  # Aurora was raised first
    assert "infra/stop_work.sh" in r.err


def test_start_plain_only_sets_max_size(sandbox):
    r = sandbox("start_work.sh", started_world())
    assert r.code == 0
    updates = [r.calls[i] for i in r.find("autoscaling", "update-auto-scaling-group")]
    assert len(updates) == 1
    assert "--max-size" in updates[0] and updates[0][updates[0].index("--max-size") + 1] == "1"
    assert "--min-size" not in updates[0] and "--desired-capacity" not in updates[0]
    assert r.find("autoscaling", "put-scheduled-update-group-action") == []
    assert r.find("rds", "modify-db-cluster") == []


def test_keep_worker_sets_timer_before_raising_group(sandbox):
    r = sandbox("start_work.sh", started_world(), "--keep-worker")
    assert r.code == 0
    timer = r.find("autoscaling", "put-scheduled-update-group-action")
    raise_ = r.find("autoscaling", "update-auto-scaling-group")
    assert len(timer) == 1 and len(raise_) == 1 and timer[0] < raise_[0]
    assert r.world["asg"] == {"min": 1, "max": 1, "desired": 1, "instances": []}
    assert r.find("rds", "modify-db-cluster") == []


def test_keep_worker_timer_failure_raises_nothing(sandbox):
    world = started_world(fail=["autoscaling put-scheduled-update-group-action"])
    r = sandbox("start_work.sh", world, "--keep-worker")
    assert r.code != 0
    assert r.find("autoscaling", "update-auto-scaling-group") == []


def test_keep_worker_and_db_raises_aurora_before_worker(sandbox):
    r = sandbox("start_work.sh", started_world(), "--keep-worker-and-db")
    assert r.code == 0
    db = r.find("rds", "modify-db-cluster")
    raise_ = r.find("autoscaling", "update-auto-scaling-group")
    assert len(db) == 1 and len(raise_) == 1 and db[0] < raise_[0]
    assert "MinCapacity=0.5" in " ".join(r.calls[db[0]])
    assert r.world["asg"]["min"] == 1


def test_keep_worker_and_db_aurora_failure_starts_no_worker(sandbox):
    world = started_world(fail=["rds modify-db-cluster"])
    r = sandbox("start_work.sh", world, "--keep-worker-and-db")
    assert r.code != 0
    assert r.find("autoscaling", "update-auto-scaling-group") == []


@pytest.mark.parametrize(
    "args",
    [
        ("--hours", "2"),
        ("--hours", "5", "--keep-worker"),
        ("--max", "3"),
        ("--keep-worker", "--hours", "0"),
    ],
)
def test_start_rejects_bad_arguments_before_any_aws_call(sandbox, args):
    r = sandbox("start_work.sh", started_world(), *args)
    assert r.code == 2
    assert r.calls == []


def test_plain_start_refuses_while_idle_alarm_in_alarm(sandbox):
    r = sandbox("start_work.sh", started_world(idle_state="ALARM"))
    assert r.code != 0
    assert "idle alarm" in r.err
    assert r.changes() == []


def test_keep_worker_allowed_while_idle_alarm_in_alarm(sandbox):
    r = sandbox("start_work.sh", started_world(idle_state="ALARM"), "--keep-worker")
    assert r.code == 0
    assert r.world["asg"]["min"] == 1


def test_start_refuses_when_group_has_more_workers_than_max(sandbox):
    world = started_world(asg={"min": 0, "max": 2, "desired": 2, "instances": ["i-a", "i-b"]})
    r = sandbox("start_work.sh", world, "--max", "1")
    assert r.code != 0
    assert r.changes() == []


# ----------------------------------------------------------------- stop_work.sh


def test_stop_healthy_end_state_is_all_stopped(sandbox):
    r = sandbox("stop_work.sh", healthy_world())
    assert r.code == 0
    assert "ALL STOPPED" in r.out
    assert "NOT CONFIRMED" not in r.err


def test_stop_nat_gateway_still_exists_not_confirmed(sandbox):
    r = sandbox("stop_work.sh", healthy_world(nats=[{"id": "nat-1", "state": "available"}]))
    assert r.code != 0
    assert "ALL STOPPED" not in r.out
    assert "NOT CONFIRMED" in r.err and "NAT Gateway" in r.err and "nat_gateway = false" in r.err


def test_stop_pending_nat_gateway_also_not_confirmed(sandbox):
    r = sandbox("stop_work.sh", healthy_world(nats=[{"id": "nat-1", "state": "pending"}]))
    assert r.code != 0 and "ALL STOPPED" not in r.out


def test_stop_elastic_ip_without_nat_not_confirmed(sandbox):
    r = sandbox("stop_work.sh", healthy_world(eips=["eipalloc-1"]))
    assert r.code != 0
    assert "ALL STOPPED" not in r.out
    assert "NOT CONFIRMED" in r.err and "Elastic IP" in r.err


def test_stop_resets_aurora_minimum_then_all_stopped(sandbox):
    r = sandbox("stop_work.sh", healthy_world(db_min="0.5"))
    modify = r.find("rds", "modify-db-cluster")
    assert len(modify) == 1 and "MinCapacity=0," in " ".join(r.calls[modify[0]])
    assert "SecondsUntilAutoPause=300" in " ".join(r.calls[modify[0]])
    assert r.code == 0 and "ALL STOPPED" in r.out


def test_stop_aurora_reset_failure_not_confirmed(sandbox):
    r = sandbox("stop_work.sh", healthy_world(db_min="0.5", fail=["rds modify-db-cluster"]))
    assert r.code != 0
    assert "ALL STOPPED" not in r.out
    assert "NOT CONFIRMED" in r.err


def test_stop_running_worker_unprotect_then_zero_then_waits(sandbox):
    world = running_worker_world(drain_polls=2)
    r = sandbox("stop_work.sh", world)
    protect = r.find("autoscaling", "set-instance-protection")
    zero = r.find("autoscaling", "update-auto-scaling-group")
    delete = r.find("autoscaling", "delete-scheduled-action")
    assert len(protect) == 1 and len(zero) == 1 and len(delete) == 1
    assert "--no-protected-from-scale-in" in r.calls[protect[0]]
    assert protect[0] < zero[0]
    call = r.calls[zero[0]]
    for flag in ("--min-size", "--max-size", "--desired-capacity"):
        assert call[call.index(flag) + 1] == "0"
    assert r.world["hold"] is False
    assert r.sleeps >= 1  # it waited for the machine to go
    assert r.code == 0 and "ALL STOPPED" in r.out


def test_stop_worker_that_never_leaves_not_confirmed(sandbox):
    r = sandbox("stop_work.sh", running_worker_world(stuck=True))
    assert r.code != 0
    assert "ALL STOPPED" not in r.out
    assert "NOT CONFIRMED" in r.err


@pytest.mark.parametrize(
    "failing",
    [
        "autoscaling describe-auto-scaling-groups",
        "rds describe-db-clusters",
        "ec2 describe-nat-gateways",
        "ec2 describe-addresses",
        "sqs get-queue-url",
        "sqs get-queue-attributes",
        "autoscaling describe-scheduled-actions",
        "ec2 describe-instances",
        "cloudwatch enable-alarm-actions",
    ],
)
def test_stop_failing_call_is_never_all_stopped(sandbox, failing):
    r = sandbox("stop_work.sh", healthy_world(fail=[failing]))
    assert r.code != 0
    assert "ALL STOPPED" not in r.out
    assert "NOT CONFIRMED" in r.err


def test_stop_unknown_machine_still_running_not_confirmed(sandbox):
    machines = [{"id": "i-gpu", "type": "g6e.xlarge", "role": "worker", "state": "running"}]
    r = sandbox("stop_work.sh", healthy_world(machines=machines, stuck=True))
    assert r.code != 0 and "ALL STOPPED" not in r.out


def test_stop_build_machine_left_running_not_confirmed(sandbox):
    machines = [{"id": "i-b", "type": "g6e.xlarge", "role": "build", "state": "running"}]
    r = sandbox("stop_work.sh", healthy_world(machines=machines))
    assert r.code != 0
    assert "ALL STOPPED" not in r.out
    assert "i-b" in r.err
    assert r.find("ec2", "terminate-instances") == []  # listed, never stopped


def test_stop_dead_letter_queue_with_messages_not_confirmed(sandbox):
    for counts in ([2, 0], [0, 1]):
        r = sandbox("stop_work.sh", healthy_world(dlq=counts))
        assert r.code != 0
        assert "ALL STOPPED" not in r.out
        assert "dead-letter" in r.err


def test_stop_alarms_missing_not_confirmed(sandbox):
    r = sandbox("stop_work.sh", healthy_world(alarms_on=2))
    assert r.code != 0 and "ALL STOPPED" not in r.out
