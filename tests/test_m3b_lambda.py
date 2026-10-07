"""M3b web Lambda launcher and import hygiene. Written first from docs/M3b_spec.md §3a, §7b, §8
and §11: `neurolens.web.lambda_handler.handler` answers a function-URL event (payload 2.0) for
/healthz, returns several Set-Cookie values as separate cookies, and builds the app once per
container; the web and webhook code never import neurolens.inference, torch or numpy.
"""

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import LambdaContext, fresh_import, function_url_event

REPO_ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = ["neurolens.inference", "torch", "numpy"]
WEB_MODULES = [
    "neurolens.web.app",
    "neurolens.web.auth",
    "neurolens.web.lambda_handler",
    "neurolens.web.stripe_webhook",
    "neurolens.results",
]


def body_of(resp):
    body = resp.get("body", "")
    return base64.b64decode(body) if resp.get("isBase64Encoded") else body.encode()


@pytest.fixture
def lambda_handler(lambda_env, idp, auth_seam):
    """The web function's module as a new container imports it: config.json and environment
    variables from lambda_env, Parameter Store in moto, HTTP faked (nothing reaches Cognito)."""
    return fresh_import("neurolens.web.lambda_handler")


@pytest.fixture
def count_create_app(monkeypatch):
    """Counts create_app calls, wherever the launcher looks it up (set before its import)."""
    from neurolens.web import app as app_module

    real = app_module.create_app
    calls = []

    def counting(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(app_module, "create_app", counting)
    return calls


def test_healthz_through_a_function_url_event_is_200(lambda_handler):
    resp = lambda_handler.handler(function_url_event("GET", "/healthz"), LambdaContext())
    assert resp["statusCode"] == 200
    assert json.loads(body_of(resp)) == {"ok": True}


def test_the_app_is_built_once_per_container(lambda_env, idp, auth_seam, count_create_app):
    module = fresh_import("neurolens.web.lambda_handler")
    for _ in range(3):
        resp = module.handler(function_url_event("GET", "/healthz"), LambdaContext())
        assert resp["statusCode"] == 200
    assert len(count_create_app) == 1
    assert count_create_app[0].get("cfg") is not None  # the launcher passes the settings


def test_the_app_is_built_from_the_settings_the_environment_gives(lambda_handler):
    """Cognito mode from NEUROLENS_AUTH_MODE: an API route needs sign-in."""
    resp = lambda_handler.handler(function_url_event("GET", "/api/me"), LambdaContext())
    assert resp["statusCode"] == 401
    assert json.loads(body_of(resp))["error"] == "not_signed_in"


def test_a_response_setting_two_cookies_returns_both(lambda_env, monkeypatch):
    from flask import Flask, make_response
    from neurolens.web import app as app_module

    def two_cookie_app(*args, **kwargs):
        app = Flask("two_cookies")

        @app.route("/two")
        def two():
            resp = make_response("ok")
            resp.set_cookie("first", "1", secure=True, httponly=True)
            resp.set_cookie("second", "2", secure=True, httponly=True)
            return resp

        return app

    monkeypatch.setattr(app_module, "create_app", two_cookie_app)
    module = fresh_import("neurolens.web.lambda_handler")
    monkeypatch.setattr(module, "create_app", two_cookie_app, raising=False)
    resp = module.handler(function_url_event("GET", "/two"), LambdaContext())
    assert resp["statusCode"] == 200
    cookies = resp.get("cookies", [])
    assert sorted(c.split("=")[0] for c in cookies) == ["first", "second"]


def test_a_request_with_two_cookies_reaches_flask_with_both(lambda_env, monkeypatch):
    from flask import Flask, jsonify, request
    from neurolens.web import app as app_module

    def echo_app(*args, **kwargs):
        app = Flask("echo")

        @app.route("/echo")
        def echo():
            return jsonify(dict(request.cookies))

        return app

    monkeypatch.setattr(app_module, "create_app", echo_app)
    module = fresh_import("neurolens.web.lambda_handler")
    monkeypatch.setattr(module, "create_app", echo_app, raising=False)
    event = function_url_event("GET", "/echo", cookies=["a=1", "b=2"])
    resp = module.handler(event, LambdaContext())
    assert json.loads(body_of(resp)) == {"a": "1", "b": "2"}


def test_the_public_address_comes_from_parameter_store_when_the_environment_has_none(
    lambda_env, ssm, idp, auth_seam, count_create_app, monkeypatch
):
    """CloudFront's address cannot be an environment variable of the function it fronts."""
    monkeypatch.delenv("NEUROLENS_PUBLIC_BASE_URL")
    ssm.put_parameter(
        Name="/neurolens/web/public_base_url", Value="https://abc.cloudfront.net", Type="String"
    )
    module = fresh_import("neurolens.web.lambda_handler")
    module.handler(function_url_event("GET", "/healthz"), LambdaContext())
    assert count_create_app[0]["cfg"]["public_base_url"] == "https://abc.cloudfront.net"


# ---------------------------------------------------------------- import hygiene


def loaded_after_import(code):
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("module", WEB_MODULES)
def test_web_and_webhook_code_imports_without_inference_torch_or_numpy(module):
    code = (
        "import importlib, json, sys; "
        f"importlib.import_module({module!r}); "
        f"print(json.dumps([m for m in {FORBIDDEN!r} if m in sys.modules]))"
    )
    assert loaded_after_import(code) == []


def test_all_web_modules_together_import_without_them():
    code = (
        "import json, sys; "
        + "".join(f"import {m}; " for m in WEB_MODULES)
        + f"print(json.dumps([m for m in {FORBIDDEN!r} if m in sys.modules]))"
    )
    assert loaded_after_import(code) == []
