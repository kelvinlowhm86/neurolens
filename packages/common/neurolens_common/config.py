"""
All configuration comes from environment variables (12-factor), parsed once here.

APP_ENV picks where the whole system runs:
  dev  — everything on one machine: Postgres container, moto (S3/SQS), web + GPU worker from venvs,
         a local reaper loop instead of the Lambdas. Values come from `.env` (see .env.example).
  aws  — the deployed system. CDK sets the plain values and points at Secrets Manager for anything
         sensitive (DB_SECRET_ARN, APP_SECRET_ARN).
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class CreditPack(BaseModel):
    id: str
    credits: int
    usd_cents: int


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    app_env: Literal["dev", "aws"] = "dev"
    aws_region: str = "ap-southeast-1"
    public_base_url: str = "http://localhost:8000"

    # ─── Database ───────────────────────────────────────────────────────────
    database_url: str | None = None
    db_secret_arn: str | None = None
    migrations_dir: Path = Path("migrations")
    db_connect_timeout_s: int = 30          # Aurora takes ~15 s to resume from 0 ACU

    # ─── App secrets (local: env vars; AWS: JSON secret at APP_SECRET_ARN) ──
    app_secret_arn: str | None = None
    flask_secret_key: str = "dev-only-change-me"
    google_client_id: str | None = None
    google_client_secret: str | None = None
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    origin_verify_secret: str | None = None
    loadtest_token: str | None = None

    # ─── Storage + queue ────────────────────────────────────────────────────
    s3_bucket: str = "neurolens-local"
    s3_endpoint_url: str | None = None          # optional S3-only override (AWS_ENDPOINT_URL covers LocalStack)
    s3_public_endpoint_url: str | None = None   # LocalStack, as seen from the browser
    sqs_queue_url: str | None = None
    sqs_dlq_url: str | None = None              # dev only: the local reaper loop drains it (AWS: Lambda)
    asg_name: str | None = None

    # ─── Limits + pricing ───────────────────────────────────────────────────
    max_video_seconds: int = 120
    max_upload_bytes: int = 250 * 1024 * 1024
    allowed_types: Annotated[list[str], NoDecode] = ["video/mp4", "video/quicktime", "video/webm"]
    max_active_jobs_per_user: int = 3
    signup_bonus_credits: int = 60
    credits_per_second: float = 1.0
    min_charge_credits: int = 5
    credit_packs: list[CreditPack] = Field(default_factory=lambda: [
        CreditPack(id="starter", credits=300, usd_cents=900),
        CreditPack(id="studio", credits=1500, usd_cents=3900),
    ])
    stripe_subscription_price_id: str | None = None
    subscription_monthly_credits: int = 1000

    # ─── Web tier ───────────────────────────────────────────────────────────
    static_dir: Path = Path("static")
    samples_json: Path = Path("data/samples.json")
    samples_source: Literal["local", "s3"] = "local"   # local: served from data_dir; s3: samples/ prefix
    data_dir: Path = Path("data")
    last_job_param: str | None = None                  # SSM parameter the reaper reads (AWS only)

    # ─── Scaling ────────────────────────────────────────────────────────────
    prewarm_enabled: bool = True
    max_workers: int = 2
    jobs_per_worker: int = 1

    # ─── Worker ─────────────────────────────────────────────────────────────
    inference_backend: Literal["tribe", "mock"] = "mock"
    silent_mode: Literal["ffmpeg", "events"] = "ffmpeg"
    models_dir: Path = Path("/models")
    scratch_dir: Path = Path("/scratch")
    tribe_repo: str = "facebook/tribev2"
    idle_shutdown_seconds: int = 300
    heartbeat_seconds: int = 30
    visibility_extend_seconds: int = 180
    mock_seconds_per_pass: float = 2.0

    # ─── AI report (LLM summary → PDF) ──────────────────────────────────────
    # openai: local machines call the OpenAI API directly (OPENAI_API_KEY in .env)
    # bedrock: AWS, Claude through Amazon Bedrock with the worker's IAM role (no key)
    # mock: fixed sample text (tests, no key); off: skip the report step
    report_llm: Literal["openai", "bedrock", "mock", "off"] = "mock"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1"
    bedrock_model: str = "anthropic.claude-opus-5-5"
    bedrock_region: str | None = None            # defaults to AWS_REGION
    report_effort: Literal["low", "medium", "high"] = "high"
    report_max_frames: int = 8

    dev_login_enabled: bool = False

    @field_validator("allowed_types", mode="before")
    @classmethod
    def _split_csv(cls, v):
        # Accept "video/mp4,video/webm" from env as well as a JSON list.
        if isinstance(v, str):
            v = v.strip()
            return json.loads(v) if v.startswith("[") else [t.strip() for t in v.split(",") if t.strip()]
        return v

    def check(self) -> "Settings":
        if self.app_env == "aws" and self.dev_login_enabled:
            raise RuntimeError("DEV_LOGIN_ENABLED must never be on in AWS")
        if not (self.database_url or self.db_secret_arn):
            raise RuntimeError("Set DATABASE_URL (local) or DB_SECRET_ARN (AWS)")
        return self


def _read_secret(arn: str) -> dict:
    import boto3  # only needed on AWS

    client = boto3.client("secretsmanager")
    return json.loads(client.get_secret_value(SecretId=arn)["SecretString"])


@lru_cache
def get_settings() -> Settings:
    """Settings with Secrets Manager values merged in (AWS) — cached for the process."""
    s = Settings()
    if s.app_secret_arn:
        secret = _read_secret(s.app_secret_arn)
        s = s.model_copy(update={k: v for k, v in secret.items() if k in Settings.model_fields})
    if not s.database_url and s.db_secret_arn:
        d = _read_secret(s.db_secret_arn)
        url = (f"postgresql://{quote(d['username'], safe='')}:{quote(d['password'], safe='')}@{d['host']}:{d.get('port', 5432)}"
               f"/{d.get('dbname', 'neurolens')}")
        s = s.model_copy(update={"database_url": url})
    return s.check()
