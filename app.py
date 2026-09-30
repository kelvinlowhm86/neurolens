"""NeuroLens launcher: builds the Flask app (loading the model) and serves it.

The code lives in the neurolens/ package; this file is kept so `python app.py` still works.
"""

import logging

from neurolens.settings import HOST, PORT
from neurolens.web.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("neurolens")

if __name__ == "__main__":
    from neurolens import inference

    app = create_app()
    samples_json = app.config["SAMPLES_JSON"]
    gpu = inference.gpu_info()
    logger.info(f"Starting NeuroLens API on http://{HOST}:{PORT}")
    logger.info(f"GPU: {gpu['device'] if gpu else 'CPU'}")
    logger.info(f"Max video duration: {app.config['MAX_DURATION']}s")
    logger.info(
        f"Samples JSON: {samples_json} ({'found' if samples_json.exists() else 'not found'})"
    )
    app.run(host=HOST, port=PORT, debug=False)
