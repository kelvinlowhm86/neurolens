"""
Run the pinned WhisperX CLI from this environment (called by the `bin/uvx` shim).

torch 2.6 made `torch.load(weights_only=True)` the default. WhisperX's bundled pyannote VAD checkpoint
stores a few config classes (omegaconf etc.) that the safe loader rejects. Rather than switching the safe
loader off, we allow-list exactly those classes; anything else in any checkpoint is still refused.

    python -m neurolens_worker.whisperx_cli <whisperx args…>
"""

import collections
import sys
import typing

import torch


def _allowed_globals() -> list:
    from omegaconf import DictConfig, ListConfig
    from omegaconf.base import ContainerMetadata, Metadata
    from omegaconf.nodes import AnyNode

    return [ListConfig, DictConfig, ContainerMetadata, Metadata, AnyNode,
            typing.Any, list, dict, int, collections.defaultdict]


def _tolerate_no_speech() -> None:
    """
    Ads with music only (or non-English speech) give WhisperX zero voice segments; the pinned
    transformers then fails on the empty batch (`inputs[0]`). No speech should mean an empty
    transcript, so short-circuit the empty case.
    """
    from whisperx.asr import FasterWhisperPipeline

    original = FasterWhisperPipeline.__call__

    def call(self, inputs, *args, **kwargs):
        inputs = list(inputs)
        if not inputs:
            return iter(())
        return original(self, inputs, *args, **kwargs)

    FasterWhisperPipeline.__call__ = call


def main() -> None:
    torch.serialization.add_safe_globals(_allowed_globals())
    _tolerate_no_speech()
    from whisperx.__main__ import cli

    sys.argv = ["whisperx", *sys.argv[1:]]
    cli()


if __name__ == "__main__":
    main()
