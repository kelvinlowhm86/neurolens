"""The web app's AWS launcher (docs/M3b_spec.md §3a); the laptop's is app.py.

Lambda calls `handler` with a function-URL event (payload 2.0), which apig-wsgi turns into a WSGI
request for Flask and back, keeping several Set-Cookie values apart. The app is built on the
container's first request, from config.json and the environment variables Terraform sets, and
reused while the container lives.
"""

from neurolens import settings
from neurolens.web.app import create_app

_handler = None


def handler(event, context):
    global _handler
    if _handler is None:
        from apig_wsgi import make_lambda_handler

        _handler = make_lambda_handler(
            create_app(cfg=settings.load_settings()), binary_support=True
        )
    return _handler(event, context)
