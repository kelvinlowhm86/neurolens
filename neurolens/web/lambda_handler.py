"""The web app's AWS launcher (docs/M3b_spec.md §3a); the laptop's is app.py.

Lambda calls `handler` with a function-URL event (payload 2.0), which apig-wsgi turns into a WSGI
request for Flask and back, keeping several Set-Cookie values apart. The app is built on the
container's first request, from config.json and the environment variables Terraform sets, and
reused while the container lives. The public address is the one exception: CloudFront's address
depends on this function, so it cannot be one of its environment variables (a Terraform cycle);
Terraform stores it in Parameter Store instead, and it is read here.
"""

from neurolens import settings
from neurolens.web.app import create_app

PUBLIC_BASE_URL_PARAMETER = "/neurolens/web/public_base_url"
_handler = None


def _load_settings():
    cfg = settings.load_settings()
    if not cfg.get("public_base_url"):
        import boto3

        ssm = boto3.client("ssm", region_name=(cfg.get("aws") or {}).get("region"))
        cfg["public_base_url"] = settings.get_parameter(ssm, PUBLIC_BASE_URL_PARAMETER)
    return cfg


def handler(event, context):
    global _handler
    if _handler is None:
        from apig_wsgi import make_lambda_handler

        _handler = make_lambda_handler(create_app(cfg=_load_settings()), binary_support=True)
    return _handler(event, context)
