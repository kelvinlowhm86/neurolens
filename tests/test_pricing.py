"""Tests for neurolens.pricing.

The formula is cents_per_block * max(1, ceil((d - ALLOWANCE_SECONDS) / block_seconds)) cents:
$1.00 per started 30-second block today, with a half-second allowance. The price comes from
config.json; the pages read it from /api/limits.
"""

import pytest
from conftest import CFG
from neurolens import pricing, settings
from neurolens.pricing import estimate_cost_cents, estimate_cost_usd

# (duration in seconds, started 30-second blocks): up to 30.5 s is one block,
# 60 s two and 120 s four.
CASES = [
    (0.2, 1),
    (1, 1),
    (30, 1),
    (30.4, 1),
    (30.5, 1),
    (30.6, 2),
    (60, 2),
    (60.4, 2),
    (60.6, 3),
    (120, 4),
]


@pytest.mark.parametrize("duration, blocks", CASES)
def test_estimate_cost_cents(duration, blocks):
    assert estimate_cost_cents(CFG, duration) == blocks * settings.cents_per_block(CFG)


def test_todays_config_is_100_cents_per_30_second_block_with_a_half_second_allowance():
    assert settings.cents_per_block(CFG) == 100
    assert settings.block_seconds(CFG) == 30
    assert pricing.ALLOWANCE_SECONDS == 0.5


def test_the_config_is_read_at_call_time(monkeypatch):
    monkeypatch.setattr(settings, "cents_per_block", lambda cfg: 137)
    assert estimate_cost_cents(CFG, 60) == 274
    assert estimate_cost_usd(CFG, 60) == 2.74


def test_estimate_cost_cents_returns_an_int():
    assert isinstance(estimate_cost_cents(CFG, 27.4), int)


@pytest.mark.parametrize("duration, blocks", CASES)
def test_estimate_cost_usd_is_cents_over_100(duration, blocks):
    assert estimate_cost_usd(CFG, duration) == blocks * settings.cents_per_block(CFG) / 100


def test_estimate_cost_usd_is_a_float():
    assert isinstance(estimate_cost_usd(CFG, 27.4), float)
    assert estimate_cost_usd(CFG, 27.4) == settings.cents_per_block(CFG) / 100
