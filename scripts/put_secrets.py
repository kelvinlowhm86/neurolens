"""
Create or update the `neurolens/app` secret in Secrets Manager from a local, git-ignored file.

    python scripts/put_secrets.py .env.deploy

.env.deploy holds only the deployed system's secrets (KEY=VALUE lines):
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET,
    LOADTEST_TOKEN (optional — enables Experiment 2 load-test sign-in)
FLASK_SECRET_KEY and ORIGIN_VERIFY_SECRET are generated here on first run and kept on later runs.
"""

import json
import secrets
import sys
from pathlib import Path

import boto3

NAME = "neurolens/app"
KEYS = {"GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "LOADTEST_TOKEN"}


def read_env(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() in KEYS and v.strip():
                out[k.strip().lower()] = v.strip()
    return out


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    values = read_env(Path(sys.argv[1]))
    sm = boto3.client("secretsmanager")
    try:
        current = json.loads(sm.get_secret_value(SecretId=NAME)["SecretString"])
        exists = True
    except sm.exceptions.ResourceNotFoundException:
        current, exists = {}, False

    merged = {**current, **values}
    merged.setdefault("flask_secret_key", secrets.token_hex(32))
    merged.setdefault("origin_verify_secret", secrets.token_urlsafe(32))
    body = json.dumps(merged)
    if exists:
        sm.put_secret_value(SecretId=NAME, SecretString=body)
    else:
        sm.create_secret(Name=NAME, SecretString=body, Description="NeuroLens web tier secrets")
    print(f"{'Updated' if exists else 'Created'} {NAME} with keys: {', '.join(sorted(merged))}")


if __name__ == "__main__":
    main()
