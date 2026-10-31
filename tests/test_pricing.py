"""Tests for neurolens.pricing. Written from docs/M1_spec.md sections 1 and 6a.

The formula is CENTS_PER_BLOCK * max(1, ceil((d - ALLOWANCE_SECONDS) / BLOCK_SECONDS)) cents:
$1.00 per started 30-second block today, with a half-second allowance. The module constants are the
one place the price lives; the pages read them from /api/limits.
"""

import math

import pytest
from neurolens import pricing
from neurolens.pricing import estimate_cost_cents, estimate_cost_usd

# (duration in seconds, started 30-second blocks), as in the spec: up to 30.5 s is one block,
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
    assert estimate_cost_cents(duration) == blocks * pricing.CENTS_PER_BLOCK


@pytest.mark.parametrize("duration, blocks", CASES)
def test_estimate_cost_cents_matches_the_written_formula(duration, blocks):
    expected = pricing.CENTS_PER_BLOCK * max(
        1, math.ceil((duration - pricing.ALLOWANCE_SECONDS) / pricing.BLOCK_SECONDS)
    )
    assert estimate_cost_cents(duration) == expected


def test_todays_constants_are_100_cents_per_30_second_block_with_a_half_second_allowance():
    assert pricing.CENTS_PER_BLOCK == 100
    assert pricing.BLOCK_SECONDS == 30
    assert pricing.ALLOWANCE_SECONDS == 0.5


def test_the_constants_are_read_at_call_time(monkeypatch):
    monkeypatch.setattr(
        pricing, "CENTS_PER_BLOCK", 137
    )  # not the real price, so a typed-in copy fails
    assert estimate_cost_cents(60) == 274
    assert estimate_cost_usd(60) == 2.74


def test_estimate_cost_cents_returns_an_int():
    assert isinstance(estimate_cost_cents(27.4), int)


@pytest.mark.parametrize("duration, blocks", CASES)
def test_estimate_cost_usd_is_cents_over_100(duration, blocks):
    assert estimate_cost_usd(duration) == blocks * pricing.CENTS_PER_BLOCK / 100


def test_estimate_cost_usd_is_a_float():
    assert isinstance(estimate_cost_usd(27.4), float)
    assert estimate_cost_usd(27.4) == pricing.CENTS_PER_BLOCK / 100
