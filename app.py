"""NeuroLens launcher: builds the Flask app from config.json and serves it.

The code lives in the neurolens/ package; this file is kept so `python app.py` still works.
The web app never runs the model: start the container (`python worker.py serve`) for analysis.
"""

import logging
import os
import sys
from urllib.parse import urlparse

from neurolens.settings import load_settings, server_address
from neurolens.web.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("neurolens")

LOCAL_ONLY_MESSAGE = (
    "python app.py runs only against the local fake stack: python infra/local_stack.py. "
    "The real .env is for the admin scripts."
)


def _local_endpoint(value):
    host = urlparse(value).hostname if value else None
    return host in ("localhost", "127.0.0.1")


if __name__ == "__main__":
    service_endpoints = [
        value for name, value in os.environ.items() if name.startswith("AWS_ENDPOINT_URL_")
    ]
    if (
        not _local_endpoint(os.environ.get("AWS_ENDPOINT_URL"))
        or any(not _local_endpoint(value) for value in service_endpoints)
        or "AWS_IGNORE_CONFIGURED_ENDPOINT_URLS" in os.environ
    ):
        print(LOCAL_ONLY_MESSAGE, file=sys.stderr)
        sys.exit(1)
    cfg = load_settings()
    app = create_app(cfg=cfg)
    host, port = server_address(cfg)
    logger.info(f"Starting NeuroLens web app on http://{host}:{port}")
    logger.info(f"Max video duration: {app.config['MAX_DURATION']}s")
    app.run(host=host, port=port, debug=False)
