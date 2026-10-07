"""Shared fixtures for the M1 tests (docs/M1_spec.md section 6a).

Everything AWS-related runs against moto, an in-memory fake of S3 and SQS. Fake credentials
are set for every test so that an accidental real AWS call fails instead of costing money.

M3a (docs/M3a_spec.md §10): database tests run against a real PostgreSQL 16 named by
NEUROLENS_TEST_DSN. Each test gets a fresh schema built from infra/migrations/*.sql, and the
ledger invariants are checked for every user when the test ends.
"""

import importlib
import os
import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

BUCKET = "neurolens-test-bucket"
ROI_NAMES = ["ffa_faces", "eba_bodies", "ppa_scenes", "sts_social", "auditory"]

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO_ROOT / "infra" / "migrations"
# The user that owns the worker tests' jobs (uploads/{user_id}/{job_id}.mp4, M3a §6).
USER_ID = "test-user"
USER_EMAIL = "test-user@example.com"
STARTER_CENTS = 500  # M3a §3b default
# The web app's development user in make_cfg (M3a §2), distinct from create_app's built-in one.
WEB_USER_ID = "web-user"
WEB_USER_EMAIL = "web-user@example.com"
LOCAL_DB_HINT = (
    "Start PostgreSQL 16 locally with: docker run -d --name neurolens-test-pg "
    "-e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16 ; then: export "
    "NEUROLENS_TEST_DSN=postgresql://postgres:test@localhost:55432/postgres"
)


@pytest.fixture(autouse=True)
def aws_env(monkeypatch, tmp_path_factory):
    """Fake credentials and no access to the developer's real AWS files or profile."""
    empty = tmp_path_factory.mktemp("aws_empty")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(empty / "credentials"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(empty / "config"))
    monkeypatch.delenv("AWS_PROFILE", raising=False)


ENV_SETTING_VARS = [
    "HF_TOKEN",
    "NEUROLENS_AWS_REGION",
    "NEUROLENS_S3_BUCKET",
    "NEUROLENS_SQS_QUEUE_URL",
    "NEUROLENS_WORKER_GROUP",  # M2b: aws.worker_group
    "NEUROLENS_DEPLOYED",  # M2b: makes validate_config require aws.worker_group
    "NEUROLENS_DB_CLUSTER_ARN",  # M3a §3a: aws.db_cluster_arn
    "NEUROLENS_DB_SECRET_ARN",  # M3a §3a: aws.db_secret_arn
    "NEUROLENS_DB_NAME",  # M3a §3a: aws.db_name
]


@pytest.fixture(autouse=True)
def isolate_env_settings(monkeypatch):
    """The developer's real .env and environment must never leak into a test.

    Removes the settings variables and turns neurolens.settings.load_dotenv into a no-op
    (load_settings and run() look it up by its module-global name). Tests of the real
    load_dotenv put the real function back themselves (tests/test_env_settings.py).
    """
    from neurolens import settings

    for name in ENV_SETTING_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "load_dotenv", lambda *a, **kw: None, raising=False)


@pytest.fixture(autouse=True)
def restore_sigterm_handler():
    """M2b: worker.run() installs a SIGTERM handler. Whatever a test does, the next test starts
    with the handler pytest had."""
    import signal

    previous = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, previous)


@pytest.fixture
def aws():
    """A moto S3 bucket and SQS queue, plus boto3 clients for them."""
    import boto3
    from moto import mock_aws

    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        sqs = boto3.client("sqs", region_name="us-east-1")
        queue_url = sqs.create_queue(QueueName="neurolens-test-queue")["QueueUrl"]
        yield SimpleNamespace(s3=s3, sqs=sqs, bucket=BUCKET, queue_url=queue_url)


@pytest.fixture
def make_cfg(aws, tmp_path):
    """Build a config dict like config.json plus the .env values, pointing at the moto resources."""
    (tmp_path / "output").mkdir(exist_ok=True)

    def make(with_aws=True, **overrides):
        cfg = {
            "hf_token": "hf_not_a_real_token",
            "paths": {
                "models": str(tmp_path / "models"),
                "data": str(tmp_path / "data"),
                "output": str(tmp_path / "output"),
            },
            "model": {
                "repo_id": "facebook/tribev2",
                "llama_repo_id": "meta-llama/Llama-3.2-3B",
                "pre_download_llama": False,
            },
            "aws": {
                "region": "us-east-1",
                "s3_bucket": aws.bucket,
                "sqs_queue_url": aws.queue_url,
            },
            "hf_download_timeout": 300,
            "max_video_duration_seconds": 120,
            "max_upload_bytes": 300000000,
            # M2b §1a: the heartbeat settings config.json gains (same values).
            "worker": {"heartbeat_seconds": 50, "max_job_minutes": 75},
            # M3a §9: the development identity, starter credit and the address the app binds to.
            # No `db` block: tests hand the database in (create_app(db=...), handle_record(db=...)).
            "auth": {"mode": "dev", "dev_user_id": WEB_USER_ID, "dev_email": WEB_USER_EMAIL},
            "billing": {"starter_cents": STARTER_CENTS},
            "server": {"host": "127.0.0.1", "port": 5003},
        }
        if not with_aws:
            del cfg["aws"]
        cfg.update(overrides)
        return cfg

    return make


def _make_clip(path, seconds=3, audio=True):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i"]
    cmd += [f"testsrc=size=160x120:rate=10:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True)


@pytest.fixture(scope="session")
def clip_path(tmp_path_factory):
    """A 3-second mp4 with an audio track, generated with ffmpeg (no binary files in git)."""
    path = tmp_path_factory.mktemp("clips") / "clip.mp4"
    _make_clip(path, seconds=3, audio=True)
    return path


@pytest.fixture(scope="session")
def clip2_path(tmp_path_factory):
    """A different 2-second mp4 (different size from clip_path)."""
    path = tmp_path_factory.mktemp("clips2") / "clip2.mp4"
    _make_clip(path, seconds=2, audio=False)
    return path


@pytest.fixture
def roi_masks_small():
    """Tiny hand-made ROI masks (100 vertices each) so nothing downloads the atlas."""
    masks = {}
    for i, name in enumerate(ROI_NAMES):
        mask = np.zeros(20484, dtype=bool)
        mask[i * 100 : (i + 1) * 100] = True
        masks[name] = mask
    return masks


@pytest.fixture
def new_key():
    """Returns (job_id, key) for a fresh upload key (M3a §6: uploads/{user_id}/{job_id}{ext}).
    No jobs row exists for it: to the M3a worker it is an unknown job."""

    def make(ext=".mp4", user_id=USER_ID):
        job_id = str(uuid.uuid4())
        return job_id, f"uploads/{user_id}/{job_id}{ext}"

    return make


@pytest.fixture
def new_job(db, new_key):
    """Returns (job_id, key) for a fresh upload key whose job is reserved in the database, as the
    presign endpoint leaves it (M3a §4a Stage 1): the user exists with the starter credit and the
    job is `queued` with the price of `client_seconds` reserved."""
    from neurolens import billing

    def make(ext=".mp4", *, client_seconds=3, user_id=USER_ID, starter_cents=STARTER_CENTS):
        job_id, key = new_key(ext, user_id=user_id)
        billing.ensure_user(db, user_id, f"{user_id}@example.com", starter_cents)
        billing.reserve(db, user_id, job_id, key, f"clip{ext}", round(client_seconds * 1000))
        return job_id, key

    return make


# ---------------------------------------------------------------- PostgreSQL (M3a §10)


@pytest.fixture(scope="session")
def pg_dsn():
    """The test server's DSN. Unset: skip, except in CI, where the billing tests must run."""
    dsn = os.environ.get("NEUROLENS_TEST_DSN")
    if not dsn:
        if os.environ.get("CI", "").lower() == "true":
            pytest.fail("NEUROLENS_TEST_DSN is not set in CI: the billing tests cannot be skipped.")
        pytest.skip(f"PostgreSQL tests skipped: NEUROLENS_TEST_DSN is not set. {LOCAL_DB_HINT}")
    return dsn


def migration_statements():
    """Every statement of infra/migrations/*.sql in order, split as apply_schema.py splits them
    (M3a §3c): a statement ends with `;` at the end of a line."""
    statements = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        current = []
        for line in path.read_text().splitlines():
            current.append(line)
            if line.rstrip().endswith(";"):
                statements.append("\n".join(current).strip())
                current = []
        assert not "\n".join(current).strip(), f"{path.name} ends without a `;`"
    assert statements, f"no migrations found in {MIGRATIONS}"
    return statements


INVARIANT_QUERIES = {
    # 1. ledger sums equal the balances row (and no ledger row belongs to a user with no balance)
    "ledger sums differ from the balance": """
        SELECT b.user_id FROM balances b
        WHERE b.available_cents <> COALESCE(
                (SELECT sum(available_delta_cents) FROM ledger l WHERE l.user_id = b.user_id), 0)
           OR b.reserved_cents <> COALESCE(
                (SELECT sum(reserved_delta_cents) FROM ledger l WHERE l.user_id = b.user_id), 0)
        UNION ALL
        SELECT DISTINCT l.user_id FROM ledger l
        WHERE NOT EXISTS (SELECT 1 FROM balances b WHERE b.user_id = l.user_id)
    """,
    # 2. no balance is negative
    "negative balance": """
        SELECT user_id FROM balances WHERE available_cents < 0 OR reserved_cents < 0
    """,
    # 3. reserved equals the sum over the user's queued and processing jobs
    "reserved differs from the open jobs": """
        SELECT b.user_id FROM balances b
        WHERE b.reserved_cents <> COALESCE((
            SELECT sum(j.reserved_cents) FROM jobs j
            WHERE j.user_id = b.user_id AND j.status IN ('queued', 'processing')), 0)
    """,
    # 4. done: one capture row, no refund; failed: one refund row and one refunds row;
    #    a job still open has neither (only the two terminal functions write them)
    "terminal job without exactly one outcome": """
        WITH c AS (
            SELECT j.job_id, j.status,
                (SELECT count(*) FROM ledger l
                 WHERE l.job_id = j.job_id AND l.kind = 'capture') AS captures,
                (SELECT count(*) FROM ledger l
                 WHERE l.job_id = j.job_id AND l.kind = 'refund') AS refund_rows,
                (SELECT count(*) FROM refunds r WHERE r.job_id = j.job_id) AS refunds
            FROM jobs j
        )
        SELECT job_id::text FROM c
        WHERE (status = 'done' AND (captures, refund_rows, refunds) <> (1, 0, 0))
           OR (status = 'failed' AND (captures, refund_rows, refunds) <> (0, 1, 1))
           OR (status IN ('queued', 'processing') AND (captures, refund_rows, refunds) <> (0, 0, 0))
    """,
}


def check_invariants(conn):
    broken = {}
    for name, sql in INVARIANT_QUERIES.items():
        rows = conn.execute(sql).fetchall()
        if rows:
            broken[name] = [r[0] for r in rows]
    assert not broken, f"M3a §10 ledger invariants broken: {broken}"


@pytest.fixture
def db_dsn(pg_dsn):
    """A DSN for a fresh schema with the migrations applied. When the test ends the four §10
    invariants are checked for every user (a failure there shows as an error of the test), then
    the schema is dropped."""
    import psycopg
    from psycopg.conninfo import make_conninfo

    schema = f"t_{uuid.uuid4().hex}"
    admin = psycopg.connect(pg_dsn, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    dsn = make_conninfo(pg_dsn, options=f"-c search_path={schema}")
    conn = psycopg.connect(dsn, autocommit=True)
    try:
        for statement in migration_statements():
            conn.execute(statement)
        yield dsn
        check_invariants(conn)
    finally:
        conn.close()
        admin.execute("SET lock_timeout = '10s'")  # a leaked open transaction fails, not hangs
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


@pytest.fixture
def db(db_dsn):
    """The application's PostgreSQL backend (M3a §3d) on the test's fresh schema."""
    from neurolens.db import PostgresDatabase

    database = PostgresDatabase(db_dsn)
    yield database
    close = getattr(database, "close", None)  # the spec gives it no close(); use one if present
    if callable(close):
        close()


class Pg:
    """Direct SQL on the test schema, for setting up states (ages, locks) and reading results
    without going through the code under test."""

    def __init__(self, conn):
        self.conn = conn

    def rows(self, sql, *params):
        cur = self.conn.execute(sql, params)
        if cur.description is None:
            return []
        names = [d.name for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]

    def one(self, sql, *params):
        rows = self.rows(sql, *params)
        return rows[0] if rows else None

    def job(self, job_id):
        row = self.one("SELECT * FROM jobs WHERE job_id = %s::uuid", str(job_id))
        if row is not None:
            row["job_id"] = str(row["job_id"])
        return row

    def balance(self, user_id=USER_ID):
        """(available_cents, reserved_cents), or None if the user has no balance row."""
        row = self.one(
            "SELECT available_cents, reserved_cents FROM balances WHERE user_id = %s", user_id
        )
        return None if row is None else (row["available_cents"], row["reserved_cents"])

    def ledger(self, user_id=USER_ID):
        rows = self.rows("SELECT * FROM ledger WHERE user_id = %s ORDER BY id", user_id)
        for row in rows:
            row["job_id"] = None if row["job_id"] is None else str(row["job_id"])
        return rows

    def kinds(self, user_id=USER_ID):
        return [row["kind"] for row in self.ledger(user_id)]

    def refund_reason(self, job_id):
        row = self.one("SELECT reason FROM refunds WHERE job_id = %s::uuid", str(job_id))
        return None if row is None else row["reason"]

    def age(self, job_id, *, updated_s=None, created_s=None):
        """Move the job's updated_at / created_at that many seconds into the past."""
        if updated_s is not None:
            self.conn.execute(
                "UPDATE jobs SET updated_at = now() - make_interval(secs => %s) "
                "WHERE job_id = %s::uuid",
                (updated_s, str(job_id)),
            )
        if created_s is not None:
            self.conn.execute(
                "UPDATE jobs SET created_at = now() - make_interval(secs => %s) "
                "WHERE job_id = %s::uuid",
                (created_s, str(job_id)),
            )

    def snapshot(self):
        """Everything money- and job-related, to show that a call changed nothing."""
        return {
            "balances": self.rows("SELECT * FROM balances ORDER BY user_id"),
            "ledger": self.rows("SELECT * FROM ledger ORDER BY id"),
            "jobs": self.rows("SELECT * FROM jobs ORDER BY job_id"),
            "refunds": self.rows("SELECT * FROM refunds ORDER BY job_id"),
        }


@pytest.fixture
def pg(db_dsn):
    import psycopg

    conn = psycopg.connect(db_dsn, autocommit=True)
    yield Pg(conn)
    conn.close()


class RowLock:
    """A third connection that holds `SELECT ... FOR UPDATE` on rows until released (M3a §10's
    deterministic concurrency): callers can wait until N other sessions are blocked behind it."""

    def __init__(self, dsn, pg):
        import psycopg

        self._conn = psycopg.connect(dsn)  # not autocommit: the lock lasts until rollback
        self._pg = pg
        self.pid = self._conn.info.backend_pid

    def execute(self, sql, *params):
        self._conn.execute(sql, params)

    def wait_for_blocked(self, count, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # A second waiter queues behind the first one's tuple lock, not the holder's, so
            # count every session of this database that waits on any lock (tests run serially).
            row = self._pg.one(
                "SELECT count(*) AS n FROM pg_stat_activity "
                "WHERE datname = current_database() AND cardinality(pg_blocking_pids(pid)) > 0"
            )
            if row["n"] >= count:
                return
            time.sleep(0.02)
        raise AssertionError(f"fewer than {count} sessions blocked behind the held lock")

    def release(self):
        self._conn.rollback()
        self._conn.close()


@pytest.fixture
def row_lock(db_dsn, pg):
    locks = []

    def make():
        lock = RowLock(db_dsn, pg)
        locks.append(lock)
        return lock

    yield make
    for lock in locks:
        if not lock._conn.closed:
            lock.release()


@pytest.fixture
def patch_everywhere(monkeypatch):
    """Replace a function wherever the implementation might look it up.

    The spec names the functions by module (for example neurolens.inference.predict) but
    not how the worker imports them, so patch the module attribute and, if present, the copy
    in the worker / web modules.
    """

    def patch(name, fn, *modules):
        patched = 0
        for modname in modules:
            mod = importlib.import_module(modname)
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, fn)
                patched += 1
        assert patched, f"{name} not found in any of {modules}"

    return patch


class SpyS3:
    """Wraps a boto3 client: records every call and refuses the listed ones.

    `forbid_on_uploads` refuses a call only when it targets an uploaded video (a key under
    uploads/). The worker also reads results/ objects (result_exists, from M2b), so "never
    download the video" cannot be checked by refusing get_object altogether.
    """

    def __init__(self, inner, forbid=(), forbid_on_uploads=()):
        self._inner = inner
        self._forbid = set(forbid)
        self._forbid_on_uploads = set(forbid_on_uploads)
        self.calls = []

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def wrapper(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name in self._forbid:
                raise AssertionError(f"s3.{name} must not be called here")
            key = kwargs.get("Key", args[1] if len(args) > 1 else None)
            if name in self._forbid_on_uploads and str(key).startswith("uploads/"):
                raise AssertionError(f"s3.{name} must not be called on {key} here")
            return attr(*args, **kwargs)

        return wrapper

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def spy_s3(aws):
    def make(forbid=(), forbid_on_uploads=()):
        return SpyS3(aws.s3, forbid=forbid, forbid_on_uploads=forbid_on_uploads)

    return make


@pytest.fixture
def queue_message(aws):
    """Put a body on the queue and receive it, like the worker would.

    VisibilityTimeout=0 makes an undeleted message visible again at once, so
    `remaining()` can tell whether process_message deleted it. The message carries its
    ApproximateReceiveCount (M2b §1a: process_message reads it; this is its first receive, "1").
    """

    def put(body):
        aws.sqs.send_message(QueueUrl=aws.queue_url, MessageBody=body)
        resp = aws.sqs.receive_message(
            QueueUrl=aws.queue_url,
            MaxNumberOfMessages=1,
            VisibilityTimeout=0,
            MessageSystemAttributeNames=["ApproximateReceiveCount"],
        )
        return resp["Messages"][0]

    return put


@pytest.fixture
def remaining(aws):
    def look():
        resp = aws.sqs.receive_message(
            QueueUrl=aws.queue_url, MaxNumberOfMessages=10, VisibilityTimeout=0
        )
        return resp.get("Messages", [])

    return look
