"""
Billing via Stripe hosted Checkout (test mode). Card data never touches our servers (PCI SAQ-A).

Credits are granted only from the signed webhook, never from the browser redirect, and each
Stripe session / invoice id can credit at most once (ledger unique index) — so webhook retries
and replays are harmless.
"""

import json
import logging

import stripe
from flask import Blueprint, current_app, request

from neurolens_common import db, ledger, pricing
from neurolens_common.config import Settings

from .auth import current_user_id, login_required
from .errors import error

logger = logging.getLogger("neurolens.web.billing")

bp = Blueprint("billing", __name__)


def _cfg() -> Settings:
    return current_app.extensions["nl_cfg"]


def _stripe_ready(cfg: Settings) -> bool:
    stripe.api_key = cfg.stripe_secret_key
    return bool(cfg.stripe_secret_key)


@bp.get("/api/billing/packs")
def packs():
    cfg = _cfg()
    return {"packs": [p.model_dump() for p in cfg.credit_packs],
            "subscription": {"available": bool(cfg.stripe_subscription_price_id),
                             "monthly_credits": cfg.subscription_monthly_credits}}


@bp.post("/api/billing/checkout")
@login_required
def checkout():
    cfg = _cfg()
    if not _stripe_ready(cfg):
        return error("BILLING_UNAVAILABLE", "Payments are not configured", 503)
    pack = pricing.find_pack((request.get_json(silent=True) or {}).get("pack_id", ""), cfg)
    if pack is None:
        return error("UNKNOWN_PACK", "Choose one of the listed credit packs", 400)
    uid = str(current_user_id())
    base = cfg.public_base_url.rstrip("/")
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{"quantity": 1, "price_data": {
            "currency": "usd", "unit_amount": pack.usd_cents,
            "product_data": {"name": f"NeuroLens {pack.credits} credits"},
        }}],
        client_reference_id=uid,
        metadata={"user_id": uid, "pack_id": pack.id, "credits": str(pack.credits)},
        success_url=f"{base}/?checkout=success",
        cancel_url=f"{base}/?checkout=cancel",
    )
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO payments (stripe_session_id, user_id, pack_id, credits, amount_cents, currency, status)
               VALUES (%s, %s, %s, %s, %s, 'usd', 'created')""",
            (session.id, uid, pack.id, pack.credits, pack.usd_cents),
        )
    return {"checkout_url": session.url}


@bp.post("/api/billing/subscribe")
@login_required
def subscribe():
    cfg = _cfg()
    if not _stripe_ready(cfg) or not cfg.stripe_subscription_price_id:
        return error("BILLING_UNAVAILABLE", "Subscriptions are not configured", 503)
    uid = str(current_user_id())
    base = cfg.public_base_url.rstrip("/")
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": cfg.stripe_subscription_price_id, "quantity": 1}],
        client_reference_id=uid,
        metadata={"user_id": uid},
        subscription_data={"metadata": {"user_id": uid}},
        success_url=f"{base}/?checkout=success",
        cancel_url=f"{base}/?checkout=cancel",
    )
    return {"checkout_url": session.url}


@bp.post("/api/billing/webhook")
def webhook():
    cfg = _cfg()
    if not _stripe_ready(cfg) or not cfg.stripe_webhook_secret:
        return error("BILLING_UNAVAILABLE", "Payments are not configured", 503)
    payload = request.get_data()
    try:
        stripe.Webhook.construct_event(payload, request.headers.get("Stripe-Signature", ""), cfg.stripe_webhook_secret)
    except (ValueError, stripe.SignatureVerificationError):
        return error("BAD_SIGNATURE", "Invalid webhook signature", 400)
    event = json.loads(payload)      # verified; work with plain dicts (independent of the SDK's object model)

    obj = event["data"]["object"]
    if event["type"] == "checkout.session.completed":
        _on_checkout_completed(cfg, obj)
    elif event["type"] == "invoice.paid":
        _on_invoice_paid(cfg, obj)
    return {"received": True}


def _on_checkout_completed(cfg: Settings, s) -> None:
    uid = (s.get("metadata") or {}).get("user_id") or s.get("client_reference_id")
    if not uid:
        logger.warning("Checkout session %s has no user id", s.get("id"))
        return
    with db.transaction() as conn:
        if s.get("mode") == "subscription":
            # Remember the customer so invoice.paid can find the user; credits come with each invoice.
            conn.execute("UPDATE users SET stripe_customer_id = %s WHERE id = %s", (s.get("customer"), uid))
            return
        if s.get("payment_status") != "paid":
            return
        credits = int(s["metadata"]["credits"])
        if ledger.grant(conn, uid, credits, "PURCHASE", s["id"]):
            conn.execute("UPDATE payments SET status = 'paid', paid_at = now() WHERE stripe_session_id = %s",
                         (s["id"],))
            logger.info("Granted %d purchased credits to %s", credits, uid)


def _on_invoice_paid(cfg: Settings, inv) -> None:
    if not inv.get("subscription"):
        return
    with db.transaction() as conn:
        row = conn.execute("SELECT id FROM users WHERE stripe_customer_id = %s", (inv.get("customer"),)).fetchone()
        if row is None:
            logger.warning("invoice.paid for unknown customer %s", inv.get("customer"))
            return
        ledger.grant(conn, row["id"], cfg.subscription_monthly_credits, "SUBSCRIPTION_GRANT", inv["id"])
