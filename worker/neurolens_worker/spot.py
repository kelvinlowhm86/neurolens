"""
EC2 instance metadata (IMDSv2) and the Spot interruption watcher.

AWS posts `spot/instance-action` about two minutes before reclaiming a Spot instance. The watcher
polls it every 5 s and sets an event the pipeline checks between stages. The launch template sets
the IMDS hop limit to 2 so this works from inside the container.
"""

import logging
import threading
import urllib.error
import urllib.request

logger = logging.getLogger("neurolens.worker.spot")

IMDS = "http://169.254.169.254/latest"


def _token(timeout: float = 2) -> str:
    req = urllib.request.Request(f"{IMDS}/api/token", method="PUT",
                                 headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()


def imds_get(path: str, timeout: float = 2) -> str | None:
    """GET a metadata path; None if it does not exist (404) or IMDS is unreachable."""
    try:
        req = urllib.request.Request(f"{IMDS}/{path}", headers={"X-aws-ec2-metadata-token": _token(timeout)})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


class SpotWatcher(threading.Thread):
    def __init__(self, interrupted: threading.Event, poll_s: float = 5):
        super().__init__(name="spot-watcher", daemon=True)
        self.interrupted = interrupted
        self.poll_s = poll_s
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.wait(self.poll_s):
            action = imds_get("meta-data/spot/instance-action")
            if action:
                logger.warning("Spot interruption notice: %s", action)
                self.interrupted.set()
                return

    def stop(self) -> None:
        self._stop_event.set()
