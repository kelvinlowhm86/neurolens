"""Tests for neurolens.pricing. Written from docs/M1_spec.md sections 1 and 6a.

The formula is 90 * max(1, ceil((d - 0.5) / 30)) cents: $0.90 per started 30-second block,
with a half-second allowance. static/ mirrors this formula in JavaScript and MUST be changed
together with it (M1 spec section 6).
"""

import math

import pytest
from neurolens.pricing import estimate_cost_cents, estimate_cost_usd

# (duration in seconds, cents). The first four are the spec's "all 90"; the rest follow the
# formula and agree with the spec's 60 s -> 180 and 120 s -> 360.
CASES = [
    (0.2, 90),
    (1, 90),
    (30, 90),
    (30.4, 90),
    (30.5, 90),
    (30.6, 180),
    (60, 180),
    (60.4, 180),
    (60.6, 270),
    (120, 360),
]


@pytest.mark.parametrize("duration, cents", CASES)
def test_estimate_cost_cents(duration, cents):
    assert estimate_cost_cents(duration) == cents


@pytest.mark.parametrize("duration, cents", CASES)
def test_estimate_cost_cents_matches_the_written_formula(duration, cents):
    assert estimate_cost_cents(duration) == 90 * max(1, math.ceil((duration - 0.5) / 30))


def test_estimate_cost_cents_returns_an_int():
    assert isinstance(estimate_cost_cents(27.4), int)


@pytest.mark.parametrize("duration, cents", CASES)
def test_estimate_cost_usd_is_cents_over_100(duration, cents):
    assert estimate_cost_usd(duration) == cents / 100


def test_estimate_cost_usd_is_a_float():
    assert isinstance(estimate_cost_usd(27.4), float)
    assert estimate_cost_usd(27.4) == 0.9
