"""The web Lambda logs one line per request (method, path, status, ms) and never the query string,
which carries the sign-in code on /auth/callback."""

import logging

from conftest import LambdaContext, fresh_import, function_url_event


def test_each_request_logs_method_path_and_status_but_not_the_query(
    lambda_env, idp, auth_seam, caplog
):
    module = fresh_import("neurolens.web.lambda_handler")
    event = function_url_event("GET", "/healthz")
    event["rawQueryString"] = "code=SECRET-SIGN-IN-CODE"
    with caplog.at_level(logging.INFO, logger="neurolens"):
        resp = module.handler(event, LambdaContext())
    assert resp["statusCode"] == 200
    lines = [r.getMessage() for r in caplog.records]
    assert any(line.startswith("GET /healthz 200 ") for line in lines)
    assert any(line.startswith("app built in ") for line in lines)
    assert not any("SECRET-SIGN-IN-CODE" in line for line in lines)
