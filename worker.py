"""NeuroLens worker launcher: `python worker.py` (set FAKE_INFERENCE=1 on a laptop).

The code lives in the neurolens/ package; this file is kept so the command stays short.
"""

import logging

from neurolens.worker import run

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

if __name__ == "__main__":
    run()
