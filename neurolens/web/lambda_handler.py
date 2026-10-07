"""The web app's AWS launcher (docs/M3b_spec.md §3a); the laptop's is app.py.

Lambda calls `handler` with a function-URL event (payload 2.0), which apig-wsgi turns into a WSGI
request for Flask and back, keeping several Set-Cookie values apart. The app is built on the
container's first request, from config.json and the environment variables Terraform sets, and
reused while the container lives. Two values are the exception: CloudFront's address, and the
Cognito app client (whose callback address contains it), depend on this function's URL, so they
cannot be its environment variables (a Terraform cycle). Terraform stores them in Parameter Store
instead, and they are read here when the environment has none.
"""

from neurolens import settings
from neurolens.web.app import create_app

# Parameter name -> (config section or None, key).
DEPLOYMENT_PARAMETERS = {
    "/neurolens/web/public_base_url": (None, "public_base_url"),
    "/neurolens/web/cognito_client_id": ("auth", "cognito_client_id"),
}
_handler = None


def _load_settings():
    cfg = settings.load_settings()
    ssm = None
    for name, (section, key) in DEPLOYMENT_PARAMETERS.items():
        target = cfg.setdefault(section, {}) if section else cfg
        if target.get(key):
            continue
        if ssm is None:
            import boto3

            ssm = boto3.client("ssm", region_name=(cfg.get("aws") or {}).get("region"))
        target[key] = settings.get_parameter(ssm, name)
    return cfg


def handler(event, context):
    global _handler
    if _handler is None:
        from apig_wsgi import make_lambda_handler

        _handler = make_lambda_handler(create_app(cfg=_load_settings()), binary_support=True)
    return _handler(event, context)
