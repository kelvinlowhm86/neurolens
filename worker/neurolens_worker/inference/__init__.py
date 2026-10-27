from neurolens_common.config import Settings

from .base import Backend


def make_backend(cfg: Settings) -> Backend:
    if cfg.inference_backend == "mock":
        from .mock import MockBackend

        return MockBackend(seconds_per_pass=cfg.mock_seconds_per_pass)
    from .tribe import TribeBackend

    return TribeBackend(cfg)
