"""Stripe test-mode webhook (docs/M3b_spec.md §7b): its own Lambda with a public function URL,
called by Stripe directly. Its lock is Stripe's signature, checked over the raw body before
anything is parsed. It credits a paid `checkout.session.completed` in test mode for an
allowlisted user, once per event and once per session, with the cents of the pack our server
named (never Stripe's amount_total).

Answers, so Stripe retries only what a retry can fix (it retries any non-2xx for up to 3 days):
400 bad or missing signature; 200 top-ups off, a refused event or a credited one; 503 while
Aurora is still waking.

Settings come as for the web function: config.json and environment variables through
neurolens.settings, secrets from Parameter Store, read on the first event of each container.
"""

import base64
import json
import logging

from neurolens import billing, settings
from neurolens import db as dbmod
from neurolens.db import DataApiDatabase, DatabaseWaking
from neurolens.web.app import STRIPE_PARAMETERS, WEB_RESUME_WAIT_S, allowlist_emails

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ACCEPTED_TYPE = "checkout.session.completed"
_container = {}  # settings, secrets and the database, built on this container's first event


def _setup():
    """Built whole or not at all, so a failed first event (a missing parameter) is retried in
    full on the next one instead of leaving half a container."""
    if _container:
        return _container
    cfg = settings.load_settings()
    stripe_cfg = cfg.get("stripe") or {}
    built = {"enabled": bool(stripe_cfg.get("enabled"))}
    if built["enabled"]:
        import boto3

        aws = cfg["aws"]
        ssm = boto3.client("ssm", region_name=aws["region"])
        built["secret"] = settings.get_parameter(ssm, STRIPE_PARAMETERS["webhook_secret"])
        built["allowlist"] = allowlist_emails(
            settings.get_parameter(ssm, STRIPE_PARAMETERS["allowlist"])
        )
        built["packs"] = dict(stripe_cfg.get("packs") or {})
        built["db"] = DataApiDatabase(
            dbmod.data_api_client(aws["region"]),
            aws["db_cluster_arn"],
            aws["db_secret_arn"],
            aws["db_name"],
            resume_wait_s=WEB_RESUME_WAIT_S,
        )
    _container.update(built)
    return _container


def _answer(status, message):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"message": message}),
    }


def _raw_body(event):
    body = event.get("body") or ""
    return base64.b64decode(body) if event.get("isBase64Encoded") else body.encode()


def _refusal(container, event):
    """Why a validly signed event credits nothing, or None to credit it."""
    if event.get("type") != ACCEPTED_TYPE:
        return f"event type {event.get('type')!r}"
    session = event["data"]["object"]
    if event.get("livemode") or session.get("livemode"):
        return "live mode"
    if session.get("payment_status") != "paid":
        return f"payment status {session.get('payment_status')!r}"
    metadata = session.get("metadata") or {}
    if metadata.get("pack") not in container["packs"]:
        return f"unknown pack {metadata.get('pack')!r}"
    email = billing.get_email(container["db"], metadata.get("user_id"))
    if email is None:
        return "unknown user"
    if email.strip().lower() not in container["allowlist"]:
        return "user not on the allowlist"
    return None


def handler(event, context):
    container = _setup()
    if not container["enabled"]:
        return _answer(200, "top-ups are off")

    import stripe

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    try:
        stripe_event = stripe.Webhook.construct_event(
            _raw_body(event), headers.get("stripe-signature", ""), container["secret"]
        )
    except (ValueError, stripe.SignatureVerificationError) as err:
        logger.warning(f"Refused: bad signature ({type(err).__name__})")
        return _answer(400, "bad signature")
    stripe_event = stripe_event.to_dict()

    try:
        refusal = _refusal(container, stripe_event)
        if refusal:
            logger.info(f"Event {stripe_event.get('id')} credits nothing: {refusal}")
            return _answer(200, "ignored")
        session = stripe_event["data"]["object"]
        user_id, pack = session["metadata"]["user_id"], session["metadata"]["pack"]
        credited = billing.credit_test_topup(
            container["db"], stripe_event["id"], session["id"], user_id, container["packs"][pack]
        )
    except DatabaseWaking:
        logger.warning(f"Event {stripe_event.get('id')}: the database is still waking")
        return _answer(503, "database waking, retry later")
    logger.info(f"Event {stripe_event['id']}: {'credited' if credited else 'already credited'}")
    return _answer(200, "ok")
