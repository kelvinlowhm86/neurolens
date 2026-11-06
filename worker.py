"""NeuroLens GPU container launcher: `python worker.py serve` (set FAKE_INFERENCE=1 on a laptop).

The code lives in the neurolens/ package; this file is the SageMaker container's entry point.
"""

import logging
import sys

from neurolens.worker import serve

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != "serve":
        raise SystemExit("usage: python worker.py serve")
    serve()
