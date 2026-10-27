"""
Run the whole NeuroLens system on this machine (APP_ENV=dev).

    python scripts/dev_stack.py up [--mock]   # start everything (mock = no GPU needed)
    python scripts/dev_stack.py down          # stop everything (Postgres data is kept in .dev/pgdata)
    python scripts/dev_stack.py status
    python scripts/dev_stack.py logs [name]   # tail one log, or list them

What runs where (nothing is installed on the host — only project venvs + one Postgres container):

    postgres  docker container from DEV_PG_IMAGE, data in .dev/pgdata      (Aurora)
    moto      .venv-dev moto_server on DEV_MOTO_PORT                        (S3 + SQS + DLQ)
    web       .venv-dev gunicorn on DEV_BIND_HOST:DEV_WEB_PORT                (Fargate + ALB + CloudFront)
    reaper    .venv-dev reaper loop, also drains the DLQ                    (both Lambdas)
    worker    WORKER_PYTHON (.venv-worker, real GPU) or .venv-dev (--mock)  (Spot GPU ASG)

Processes are tracked by PID in .dev/pids.json and stopped by process group.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / ".dev"
LOGS = DEV / "logs"
PIDS = DEV / "pids.json"
DEV_PY = ROOT / ".venv-dev" / "bin" / "python"
PG_CONTAINER = "neurolens-dev-pg"
ORDER = ["moto", "web", "reaper", "worker"]


def load_env() -> dict[str, str]:
    path = ROOT / ".env"
    if not path.exists():
        sys.exit("No .env — run: cp .env.example .env   (then edit it)")
    env = dict(os.environ)
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "web"), str(ROOT / "worker")])
    env["PYTHONUNBUFFERED"] = "1"
    env["TMPDIR"] = str(DEV / "tmp")
    env.update(public_urls(env))
    return env


def public_urls(env: dict) -> dict[str, str]:
    """
    Everything browser-facing follows from DEV_PUBLIC_HOST + DEV_WEB_PORT (default localhost:8000):
    the app URL, the S3 upload URL the browser posts to, and the bind address (all interfaces when
    the host is not localhost, so other machines can connect). Postgres always stays on localhost.
    """
    host = env.get("DEV_PUBLIC_HOST") or "localhost"
    port = env.get("DEV_WEB_PORT") or "8000"
    moto = env.get("DEV_MOTO_PORT") or "5055"
    local = host in ("localhost", "127.0.0.1")
    return {
        "PUBLIC_BASE_URL": f"http://{host}:{port}",
        "S3_PUBLIC_ENDPOINT_URL": f"http://{host}:{moto}",
        "DEV_BIND_HOST": "127.0.0.1" if local else "0.0.0.0",
        "DEV_WEB_PORT": port,
    }


def _pids() -> dict[str, int]:
    return json.loads(PIDS.read_text()) if PIDS.exists() else {}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _start(name: str, cmd: list[str], env: dict) -> None:
    pids = _pids()
    if name in pids and _alive(pids[name]):
        print(f"  {name:<8} already running (pid {pids[name]})")
        return
    log = open(LOGS / f"{name}.log", "ab")
    p = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    pids[name] = p.pid
    PIDS.write_text(json.dumps(pids, indent=2))
    print(f"  {name:<8} started (pid {p.pid}) → .dev/logs/{name}.log")


def _wait_http(url: str, timeout: float, name: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except Exception:
            time.sleep(0.5)
    sys.exit(f"{name} did not come up at {url} — see .dev/logs/{name}.log")


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


def start_postgres(env: dict) -> None:
    state = _docker("inspect", "-f", "{{.State.Running}}", PG_CONTAINER, check=False)
    if state.returncode != 0:
        (DEV / "pgdata").mkdir(parents=True, exist_ok=True)
        _docker("run", "-d", "--name", PG_CONTAINER,
                "-e", "POSTGRES_USER=neurolens", "-e", "POSTGRES_PASSWORD=neurolens", "-e", "POSTGRES_DB=neurolens",
                "-p", f"127.0.0.1:{env.get('DEV_PG_PORT', '5433')}:5432",
                "-v", f"{DEV / 'pgdata'}:/var/lib/postgresql/data", env.get("DEV_PG_IMAGE", "postgres:15"))
        print(f"  postgres created ({env.get('DEV_PG_IMAGE', 'postgres:15')}, data in .dev/pgdata)")
    elif state.stdout.strip() != "true":
        _docker("start", PG_CONTAINER)
        print("  postgres started")
    else:
        print("  postgres already running")
    for _ in range(60):
        if _docker("exec", PG_CONTAINER, "pg_isready", "-U", "neurolens", check=False).returncode == 0:
            return
        time.sleep(1)
    sys.exit("Postgres did not become ready — check `docker logs neurolens-dev-pg`")


def up(mock: bool) -> None:
    env = load_env()
    for d in (LOGS, DEV / "tmp", ROOT / env.get("SCRATCH_DIR", ".dev/scratch")):
        d.mkdir(parents=True, exist_ok=True)
    if not DEV_PY.exists():
        sys.exit("No .venv-dev — run: make venv-dev")
    worker_py = DEV_PY if mock else ROOT / env.get("WORKER_PYTHON", ".venv-worker/bin/python")
    if not worker_py.exists():
        sys.exit(f"No GPU worker venv at {worker_py} — run: make venv-worker   (or: make dev-mock)")
    if mock:
        env["INFERENCE_BACKEND"] = "mock"

    print(f"NeuroLens dev stack ({'mock worker' if mock else 'GPU worker'})")
    start_postgres(env)

    moto_port = env.get("DEV_MOTO_PORT", "5055")
    # DEV_BIND_HOST=0.0.0.0 makes the app reachable from other machines on the LAN (Postgres stays local).
    bind = env.get("DEV_BIND_HOST", "127.0.0.1")
    web_port = env.get("DEV_WEB_PORT", "8000")
    _start("moto", [str(ROOT / ".venv-dev/bin/moto_server"), "-H", bind, "-p", moto_port], env)
    _wait_http(f"http://127.0.0.1:{moto_port}/moto-api/", 30, "moto")
    subprocess.run([str(DEV_PY), "scripts/dev_aws_init.py"], cwd=ROOT, env=env, check=True)

    _start("web", [str(ROOT / ".venv-dev/bin/gunicorn"), "-b", f"{bind}:{web_port}", "-w", "2", "--threads", "4", "--keep-alive", "75",
                   "--access-logfile", "-", "neurolens_web:create_app()"], env)
    _wait_http(f"http://127.0.0.1:{web_port}/healthz", 60, "web")
    _start("reaper", [str(DEV_PY), "-m", "neurolens_common.reaper_loop"], env)
    _start("worker", [str(worker_py), "-m", "neurolens_worker", "run"], env)
    print(f"\nOpen {env.get('PUBLIC_BASE_URL', 'http://localhost:' + web_port)}   "
          "(worker log: python scripts/dev_stack.py logs worker)")
    if not mock:
        print("The GPU worker takes ~1–2 min to load TRIBE v2 before it takes jobs.")


def down() -> None:
    pids = _pids()
    for name in reversed(ORDER):
        pid = pids.get(name)
        if pid and _alive(pid):
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            for _ in range(20):
                if not _alive(pid):
                    break
                time.sleep(0.25)
            if _alive(pid):
                os.killpg(pid, signal.SIGKILL)
            print(f"  {name:<8} stopped")
    PIDS.unlink(missing_ok=True)
    if _docker("inspect", PG_CONTAINER, check=False).returncode == 0:
        _docker("stop", PG_CONTAINER, check=False)
        print("  postgres stopped (data kept in .dev/pgdata)")


def status() -> None:
    pids = _pids()
    pg = _docker("inspect", "-f", "{{.State.Status}}", PG_CONTAINER, check=False)
    print(f"  {'postgres':<8} {pg.stdout.strip() if pg.returncode == 0 else 'not created'}")
    for name in ORDER:
        pid = pids.get(name)
        print(f"  {name:<8} {'running (pid %d)' % pid if pid and _alive(pid) else 'stopped'}")


def logs(name: str | None) -> None:
    if not name:
        for p in sorted(LOGS.glob("*.log")):
            print(p.relative_to(ROOT))
        return
    os.execvp("tail", ["tail", "-n", "100", "-f", str(LOGS / f"{name}.log")])


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("up")
    u.add_argument("--mock", action="store_true", help="fake inference; no GPU or worker venv needed")
    sub.add_parser("down")
    sub.add_parser("status")
    lg = sub.add_parser("logs")
    lg.add_argument("name", nargs="?")
    a = ap.parse_args()
    {"up": lambda: up(a.mock), "down": down, "status": status, "logs": lambda: logs(a.name)}[a.cmd]()


if __name__ == "__main__":
    main()
