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

if __name__ == "__main__":
    endpoint = os.environ.get("AWS_ENDPOINT_URL")
    host = urlparse(endpoint).hostname if endpoint else None
    if host not in ("localhost", "127.0.0.1"):
        print(LOCAL_ONLY_MESSAGE, file=sys.stderr)
        sys.exit(1)
    cfg = load_settings()
    app = create_app(cfg=cfg)
    host, port = server_address(cfg)
    logger.info(f"Starting NeuroLens web app on http://{host}:{port}")
    logger.info(f"Max video duration: {app.config['MAX_DURATION']}s")
    app.run(host=host, port=port, debug=False)
