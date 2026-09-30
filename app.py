"""NeuroLens launcher: builds the Flask app from config.json and serves it.

The code lives in the neurolens/ package; this file is kept so `python app.py` still works.
The web app never runs the model: start the worker (`python worker.py`) for analysis.
"""

import logging

from neurolens.settings import HOST, PORT, load_config
from neurolens.web.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("neurolens")

if __name__ == "__main__":
    app = create_app(cfg=load_config())
    samples_json = app.config["SAMPLES_JSON"]
    logger.info(f"Starting NeuroLens web app on http://{HOST}:{PORT}")
    logger.info(f"Max video duration: {app.config['MAX_DURATION']}s")
    logger.info(
        f"Samples JSON: {samples_json} ({'found' if samples_json.exists() else 'not found'})"
    )
    app.run(host=HOST, port=PORT, debug=False)
