"""M3b Stripe test-mode top-ups (full treatment: money). Written first from docs/M3b_spec.md §2b,
§7b, §8 and §11: `can_top_up`, the checkout endpoint (server-defined amount and metadata, never
taken from the request; each refusal) with Stripe's API faked over HTTP, and the webhook
function's answers: signature checked over the raw body (base64-decoded first) before anything
else, refused events answered 200 without credit, a still-waking database 503, and each event or
session credited once. Webhook payloads are signed here with Stripe's scheme and the test secret.
"""

import json
import time
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest
from conftest import (
    ALLOWED_EMAIL,
    CHECKOUT_URL,
    ISSUER,
    MISSING,
    PACKS,
    PUBLIC_BASE_URL,
    STRIPE_SECRET_KEY,
    STRIPE_SESSIONS_URL,
    LambdaContext,
    fresh_import,
    function_url_event,
    sign_in,
    stripe_signature,
)
from neurolens import billing
from neurolens import db as dbmod
from neurolens.web.app import create_app

STRANGER_EMAIL = "stranger@example.com"


class ExplodingDatabase:
    """A Database whose every use fails the test."""

    def transaction(self):
        raise AssertionError("the database must not be used here")


# ---------------------------------------------------------------- the checkout endpoint


@pytest.fixture
def stripe_api(fake_http):
    """Stripe's Checkout Session API over HTTP. Returns a function listing the requests made."""
    import responses

    def create_session(request):
        body = {"id": "cs_test_fake", "object": "checkout.session", "url": CHECKOUT_URL}
        return 200, {"Content-Type": "application/json"}, json.dumps(body)

    fake_http.add_callback(responses.POST, STRIPE_SESSIONS_URL, callback=create_session)

    def requests_made():
        return [c.request for c in fake_http.calls if "api.stripe.com" in c.request.url]

    return requests_made


def form_of(request):
    body = request.body.decode() if isinstance(request.body, bytes) else request.body
    return parse_qs(body)


def values(form, word):
    return [v for key, vs in form.items() if word in key for v in vs]


def checkout(client, **body):
    return client.post("/api/top-up/checkout", json=body)


@pytest.mark.parametrize("pack", list(PACKS))
def test_checkout_creates_a_session_for_the_packs_amount_and_returns_only_its_url(
    cognito_client, idp, stripe_api, pack
):
    sign_in(cognito_client, idp, email=ALLOWED_EMAIL)
    user_id = cognito_client.get("/api/me").get_json()["user_id"]
    resp = checkout(cognito_client, pack=pack)
    assert resp.status_code == 200
    assert list(resp.get_json().values()) == [CHECKOUT_URL]
    [request] = stripe_api()
    assert request.headers["Authorization"] == f"Bearer {STRIPE_SECRET_KEY}"
    form = form_of(request)
    assert values(form, "amount") == [str(PACKS[pack])]
    assert set(values(form, "currency")) == {"usd"}
    assert form["metadata[user_id]"] == [user_id]
    assert form["metadata[pack]"] == [pack]
    for name in ("success_url", "cancel_url"):
        assert form[name][0].startswith(f"{PUBLIC_BASE_URL}/"), form[name]


def test_checkout_does_not_name_payment_methods(cognito_client, idp, stripe_api):
    """Stripe rejects `payment_method_types` on newer accounts (seen on the first live call):
    payment methods are chosen in the Stripe dashboard instead (card only, M3b §7b)."""
    sign_in(cognito_client, idp, email=ALLOWED_EMAIL)
    assert checkout(cognito_client, pack="5").status_code == 200
    [request] = stripe_api()
    assert not [name for name in form_of(request) if name.startswith("payment_method_types")]


def test_checkout_never_takes_an_amount_user_or_session_from_the_request(
    cognito_client, idp, stripe_api
):
    sign_in(cognito_client, idp, email=ALLOWED_EMAIL)
    user_id = cognito_client.get("/api/me").get_json()["user_id"]
    resp = checkout(
        cognito_client,
        pack="5",
        amount=1,
        unit_amount=1,
        cents=1,
        currency="jpy",
        user_id="someone-else",
        session_id="cs_test_chosen",
        success_url="https://evil.example/",
        metadata={"user_id": "someone-else", "pack": "10"},
    )
    assert resp.status_code == 200
    form = form_of(stripe_api()[0])
    assert values(form, "amount") == ["500"]
    assert set(values(form, "currency")) == {"usd"}
    assert form["metadata[user_id]"] == [user_id]
    assert form["metadata[pack]"] == ["5"]
    assert "evil.example" not in json.dumps(form)
    assert "cs_test_chosen" not in json.dumps(form)


def test_checkout_refuses_a_user_not_on_the_allowlist(cognito_client, idp, stripe_api):
    sign_in(cognito_client, idp, email=STRANGER_EMAIL)
    resp = checkout(cognito_client, pack="5")
    assert resp.status_code == 403
    assert resp.get_json()["error"] == "top_up_not_allowed"
    assert stripe_api() == []


@pytest.mark.parametrize("pack", ["7", "50", "500"])
def test_checkout_refuses_an_unknown_pack(cognito_client, idp, stripe_api, pack):
    sign_in(cognito_client, idp, email=ALLOWED_EMAIL)
    resp = checkout(cognito_client, pack=pack)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "unknown_pack"
    assert stripe_api() == []


def test_checkout_is_404_when_stripe_is_disabled(cognito_app, idp, stripe_api):
    client = cognito_app(stripe={"enabled": False, "packs": dict(PACKS)}).test_client()
    sign_in(client, idp, email=ALLOWED_EMAIL)
    resp = checkout(client, pack="5")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"
    assert stripe_api() == []


def test_checkout_is_404_in_dev_mode(aws, db, make_cfg, tmp_path, stripe_api):
    cfg = make_cfg(stripe={"enabled": True, "packs": dict(PACKS)})
    app = create_app(cfg=cfg, data_dir=tmp_path / "data", db=db, s3_client=aws.s3)
    resp = checkout(app.test_client(), pack="5")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"
    assert stripe_api() == []


# ---------------------------------------------------------------- can_top_up


def test_can_top_up_is_true_for_an_allowlisted_user(cognito_client, idp):
    sign_in(cognito_client, idp, email=ALLOWED_EMAIL)
    assert cognito_client.get("/api/me").get_json()["can_top_up"] is True


def test_can_top_up_is_false_for_a_user_not_on_the_allowlist(cognito_client, idp):
    sign_in(cognito_client, idp, email=STRANGER_EMAIL)
    assert cognito_client.get("/api/me").get_json()["can_top_up"] is False


def test_the_allowlist_ignores_letter_case_and_spaces(cognito_app, ssm, idp):
    """§7b: a capital letter or a space typed into the parameter must not block a teammate."""
    ssm.put_parameter(
        Name="/neurolens/web/topup_allowlist",
        Value=f" {ALLOWED_EMAIL.upper()} , other-team-member@example.com",
        Type="SecureString",
        Overwrite=True,
    )
    client = cognito_app().test_client()
    sign_in(client, idp, email=ALLOWED_EMAIL)
    assert client.get("/api/me").get_json()["can_top_up"] is True


def test_can_top_up_is_false_when_stripe_is_disabled(cognito_app, idp):
    client = cognito_app(stripe={"enabled": False, "packs": dict(PACKS)}).test_client()
    sign_in(client, idp, email=ALLOWED_EMAIL)
    assert client.get("/api/me").get_json()["can_top_up"] is False


# ---------------------------------------------------------------- the webhook function


@pytest.fixture
def webhook(lambda_env, db, patch_everywhere, fake_http):
    """The webhook Lambda as AWS runs it: config.json and environment variables (lambda_env),
    Parameter Store in moto, and the Data API replaced by the test database. `use_database`
    swaps in another database; `deliver(...)` signs and sends one event."""
    module = fresh_import("neurolens.web.stripe_webhook")
    state = {"db": db}
    real_data_api = dbmod.DataApiDatabase

    def data_api_database(*args, **kwargs):
        made = state["db"]
        return made(*args, **kwargs) if callable(made) else made

    patch_everywhere(
        "DataApiDatabase", data_api_database, "neurolens.db", "neurolens.web.stripe_webhook"
    )

    def deliver(event=None, *, raw=None, secret=None, signature=None, base64_body=False):
        payload = raw if raw is not None else json.dumps(event).encode()
        headers = {"Content-Type": "application/json"}
        if signature is None:
            signature = stripe_signature(payload, **({"secret": secret} if secret else {}))
        if signature is not MISSING:
            headers["Stripe-Signature"] = signature
        event_ = function_url_event(
            "POST", "/", body=payload, headers=headers, base64_body=base64_body
        )
        return module.handler(event_, LambdaContext())

    def use_database(database):
        state["db"] = database

    return SimpleNamespace(
        deliver=deliver,
        use_database=use_database,
        write_config=lambda_env.write_config,
        real_data_api=real_data_api,
    )


def status(resp):
    return resp["statusCode"]


def checkout_completed(user_id, *, pack="5", event_id=None, session_id=None, **changes):
    """A `checkout.session.completed` event as Stripe sends it in test mode."""
    session = {
        "id": session_id or f"cs_test_{uuid.uuid4().hex}",
        "object": "checkout.session",
        "mode": "payment",
        "status": "complete",
        "payment_status": "paid",
        "amount_total": PACKS.get(pack, 100),
        "currency": "usd",
        "livemode": False,
        "metadata": {"user_id": user_id, "pack": pack},
    }
    event = {
        "id": event_id or f"evt_{uuid.uuid4().hex}",
        "object": "event",
        "api_version": "2025-01-27.acacia",
        "created": int(time.time()),
        "type": "checkout.session.completed",
        "livemode": False,
        "data": {"object": session},
    }
    event.update(changes)
    return event


@pytest.fixture
def team(db):
    return billing.sign_in(db, ISSUER, "sub-team", ALLOWED_EMAIL, 0)


@pytest.fixture
def stranger(db):
    return billing.sign_in(db, ISSUER, "sub-stranger", STRANGER_EMAIL, 0)


def stripe_events(pg):
    return pg.rows("SELECT event_id, session_id, user_id, cents FROM stripe_test_events")


@pytest.mark.parametrize("pack", list(PACKS))
def test_an_accepted_event_credits_the_packs_cents_once(webhook, team, pg, pack):
    event = checkout_completed(team, pack=pack)
    assert status(webhook.deliver(event)) == 200
    assert pg.balance(team) == (PACKS[pack], 0)
    assert pg.kinds(team) == ["test_topup"]
    assert stripe_events(pg) == [
        {
            "event_id": event["id"],
            "session_id": event["data"]["object"]["id"],
            "user_id": team,
            "cents": PACKS[pack],
        }
    ]


def test_the_same_event_twice_credits_once(webhook, team, pg):
    event = checkout_completed(team)
    assert status(webhook.deliver(event)) == 200
    assert status(webhook.deliver(event)) == 200
    assert pg.balance(team) == (500, 0)
    assert pg.kinds(team) == ["test_topup"]


def test_the_same_session_in_two_events_credits_once(webhook, team, pg):
    first = checkout_completed(team, session_id="cs_test_same")
    second = checkout_completed(team, session_id="cs_test_same")
    assert first["id"] != second["id"]
    assert status(webhook.deliver(first)) == 200
    assert status(webhook.deliver(second)) == 200
    assert pg.balance(team) == (500, 0)
    assert len(stripe_events(pg)) == 1


def test_a_base64_encoded_body_verifies_and_credits(webhook, team, pg):
    assert status(webhook.deliver(checkout_completed(team), base64_body=True)) == 200
    assert pg.balance(team) == (500, 0)


def bad_signatures(payload):
    return {
        "wrong secret": stripe_signature(payload, secret="whsec_someone_else"),
        "missing": MISSING,
        "garbage": "t=1,v1=00",
        "empty": "",
    }


@pytest.mark.parametrize("case", ["wrong secret", "missing", "garbage", "empty"])
def test_a_bad_or_missing_signature_is_400_and_does_nothing(webhook, team, pg, case):
    webhook.use_database(ExplodingDatabase())
    event = checkout_completed(team)
    payload = json.dumps(event).encode()
    resp = webhook.deliver(raw=payload, signature=bad_signatures(payload)[case])
    assert status(resp) == 400


def test_a_body_changed_after_signing_is_400(webhook, team, pg):
    webhook.use_database(ExplodingDatabase())
    signed = json.dumps(checkout_completed(team, pack="5")).encode()
    tampered = signed.replace(b'"pack": "5"', b'"pack": "10"')
    assert tampered != signed
    assert status(webhook.deliver(raw=tampered, signature=stripe_signature(signed))) == 400


def test_the_signature_is_checked_over_the_raw_body(webhook, team, pg):
    """Re-serialising the parsed JSON would change these bytes and break the signature."""
    event = checkout_completed(team)
    raw = json.dumps(event, indent=3, separators=(" ,", " :  ")).encode()
    assert status(webhook.deliver(raw=raw)) == 200
    assert pg.balance(team) == (500, 0)


def unpaid(event):
    event["data"]["object"]["payment_status"] = "unpaid"
    return event


def refused_events(team, stranger):
    return {
        "not paid": unpaid(checkout_completed(team)),
        "livemode": checkout_completed(team, livemode=True),
        "other event type": checkout_completed(team, type="payment_intent.succeeded"),
        "unknown pack": checkout_completed(team, pack="7"),
        "unknown user": checkout_completed(str(uuid.uuid4())),
        "not on the allowlist": checkout_completed(stranger),
    }


@pytest.mark.parametrize(
    "case",
    [
        "not paid",
        "livemode",
        "other event type",
        "unknown pack",
        "unknown user",
        "not on the allowlist",
    ],
)
def test_a_valid_but_refused_event_is_200_with_no_credit(webhook, team, stranger, pg, case):
    before = pg.snapshot()
    resp = webhook.deliver(refused_events(team, stranger)[case])
    assert status(resp) == 200
    assert pg.snapshot() == before
    assert stripe_events(pg) == []


def test_with_stripe_disabled_a_valid_event_is_200_and_does_nothing(webhook, team, pg):
    webhook.write_config(stripe={"enabled": False, "packs": dict(PACKS)})
    webhook.use_database(ExplodingDatabase())
    assert status(webhook.deliver(checkout_completed(team))) == 200


def test_a_database_still_waking_is_503_so_stripe_retries(webhook, team, pg, waking_aurora):
    client, _ = waking_aurora
    real = webhook.real_data_api

    def waking_database(*args, **kwargs):
        return real(client, *args[1:], **kwargs)

    webhook.use_database(waking_database)
    resp = webhook.deliver(checkout_completed(team))
    assert status(resp) == 503
    assert pg.balance(team) == (0, 0)
    assert stripe_events(pg) == []
