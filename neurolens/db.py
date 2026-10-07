"""The database layer (M3a §3d): one small interface, two backends running identical SQL.

- `DataApiDatabase`: Aurora through the RDS Data API (workers, Lambdas, web on AWS).
- `PostgresDatabase`: plain PostgreSQL through psycopg 3 (tests and laptop).

`Database.transaction()` yields an object with `execute(sql, params) -> list[dict]`; it commits
on normal exit and rolls back on an exception. SQL uses `:name` parameters and `CAST(x AS type)`,
never `::`, and contains no `%` and no string literals (values always go in as parameters), so
both backends read it the same way. Both return the same Python types per column: int, str,
bool, None, timezone-aware UTC datetime for timestamptz, str for uuid, parsed objects for jsonb.

Pure Python plus boto3 (Lambdas import this): psycopg is imported only inside PostgresDatabase.
"""

import json
import logging
import re
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from botocore.exceptions import ClientError

logger = logging.getLogger("neurolens")

# `:name` but not the second colon of a `::` cast (application SQL never uses `::` anyway).
_PARAM = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")


class DatabaseWaking(Exception):
    """Aurora was still resuming from auto-pause when the wait budget ran out."""


def check_sql(sql):
    """Refuse `%` and string literals in application SQL (§3d), on both backends alike, so a
    statement that works on a laptop never fails differently on AWS."""
    if "%" in sql:
        raise ValueError(f"application SQL must not contain '%': {sql!r}")
    if "'" in sql:
        raise ValueError(f"application SQL must not contain string literals: {sql!r}")


def to_pyformat(sql):
    """`:name` -> `%(name)s` for psycopg. Raises on `%` or a string literal (see check_sql)."""
    check_sql(sql)
    return _PARAM.sub(r"%(\1)s", sql)


# ---------------------------------------------------------------- PostgreSQL


class PostgresDatabase:
    """psycopg 3, one connection per transaction (simple and safe across threads)."""

    def __init__(self, dsn):
        self._dsn = dsn

    @contextmanager
    def transaction(self):
        import psycopg
        from psycopg.rows import dict_row

        # The connection's context manager commits on normal exit, rolls back on an exception,
        # and closes the connection either way.
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            yield _PostgresTransaction(conn)


class _PostgresTransaction:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        return self._run(to_pyformat(sql), params or {})

    def execute_unchecked(self, sql):
        """Run a statement as-is, with no parameters (migrations only, §3c)."""
        return self._run(sql, None)

    def _run(self, sql, params):
        cur = self._conn.execute(sql, params)
        if cur.description is None:
            return []
        return [{k: _pg_value(v) for k, v in row.items()} for row in cur.fetchall()]


def _pg_value(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(UTC)
    return value


# ---------------------------------------------------------------- RDS Data API


class DataApiDatabase:
    """Aurora through the RDS Data API. Only waking from auto-pause is retried (§3d): a failed
    commit, whose outcome is unknown, is raised to the caller, and the billing guards make the
    caller's retry safe."""

    def __init__(self, rds_data_client, cluster_arn, secret_arn, database, resume_wait_s=60):
        self._client = rds_data_client
        self._arns = {"resourceArn": cluster_arn, "secretArn": secret_arn}
        self._database = database
        self._resume_wait_s = resume_wait_s

    @contextmanager
    def transaction(self):
        transaction_id = self._begin()
        try:
            yield _DataApiTransaction(self, transaction_id)
        except BaseException:
            try:
                self._client.rollback_transaction(**self._arns, transactionId=transaction_id)
            except Exception:
                logger.exception("rollback failed (AWS rolls back after 3 idle minutes)")
            raise
        self._client.commit_transaction(**self._arns, transactionId=transaction_id)

    def _begin(self):
        deadline = time.monotonic() + self._resume_wait_s
        delay = 1
        while True:
            try:
                resp = self._client.begin_transaction(**self._arns, database=self._database)
                return resp["transactionId"]
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "DatabaseResumingException":
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DatabaseWaking(
                        f"the database did not wake within {self._resume_wait_s} s"
                    ) from exc
                logger.info("database is resuming from pause; retrying in %s s", delay)
                time.sleep(min(delay, remaining))
                delay *= 2

    def _execute(self, transaction_id, sql, parameters):
        kwargs = {"parameters": parameters} if parameters else {}
        resp = self._client.execute_statement(
            **self._arns,
            database=self._database,
            transactionId=transaction_id,
            sql=sql,
            includeResultMetadata=True,
            **kwargs,
        )
        columns = resp.get("columnMetadata")
        if not columns:
            return []
        names = [c.get("label") or c["name"] for c in columns]
        types = [c.get("typeName", "").lower() for c in columns]
        return [
            {
                name: _from_field(field, type_name)
                for name, type_name, field in zip(names, types, record, strict=True)
            }
            for record in resp.get("records", [])
        ]


class _DataApiTransaction:
    def __init__(self, database, transaction_id):
        self._db = database
        self._id = transaction_id

    def execute(self, sql, params=None):
        check_sql(sql)
        parameters = [_to_parameter(k, v) for k, v in (params or {}).items()]
        return self._db._execute(self._id, sql, parameters)

    def execute_unchecked(self, sql):
        """Run a statement as-is, with no parameters (migrations only, §3c)."""
        return self._db._execute(self._id, sql, None)


def _to_parameter(name, value):
    if value is None:
        field = {"isNull": True}
    elif isinstance(value, bool):  # before int: bool is a subclass of int
        field = {"booleanValue": value}
    elif isinstance(value, int):
        field = {"longValue": value}
    elif isinstance(value, float):
        field = {"doubleValue": value}
    elif isinstance(value, str):
        field = {"stringValue": value}
    else:
        raise TypeError(f"unsupported parameter type for :{name}: {type(value).__name__}")
    return {"name": name, "value": field}


def _from_field(field, type_name):
    if field.get("isNull"):
        return None
    for key in ("longValue", "booleanValue", "doubleValue"):
        if key in field:
            return field[key]
    if "stringValue" not in field:
        raise TypeError(f"unsupported Data API field for a {type_name} column: {field}")
    text = field["stringValue"]
    if type_name == "timestamptz":
        value = datetime.fromisoformat(text)  # zone-less UTC text, e.g. 2026-10-14 03:22:10.123
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if type_name in ("jsonb", "json"):
        return json.loads(text)
    return text  # text, uuid and anything else carried as a string


# ---------------------------------------------------------------- config


def from_config(cfg):
    """The configured backend: `db.backend` is "data_api" (AWS) or "postgres" (laptop, tests)."""
    db_cfg = cfg.get("db") or {}
    backend = db_cfg.get("backend")
    if backend == "postgres":
        dsn = db_cfg.get("dsn")
        if not dsn:
            raise ValueError("Missing required setting: db.dsn (NEUROLENS_DB_DSN in .env).")
        return PostgresDatabase(dsn)
    if backend == "data_api":
        aws = cfg.get("aws") or {}
        names = {
            "region": "aws.region (NEUROLENS_AWS_REGION)",
            "db_cluster_arn": "aws.db_cluster_arn (NEUROLENS_DB_CLUSTER_ARN)",
            "db_secret_arn": "aws.db_secret_arn (NEUROLENS_DB_SECRET_ARN)",
            "db_name": "aws.db_name (NEUROLENS_DB_NAME)",
        }
        missing = [label for key, label in names.items() if not aws.get(key)]
        if missing:
            raise ValueError(f"Missing required settings for the Data API: {', '.join(missing)}")
        import boto3

        client = boto3.client("rds-data", region_name=aws["region"])
        return DataApiDatabase(client, aws["db_cluster_arn"], aws["db_secret_arn"], aws["db_name"])
    raise ValueError(f"db.backend must be 'data_api' or 'postgres', not {backend!r}")
