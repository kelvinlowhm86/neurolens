"""The cost formula, shared by the web app, the worker and (from M3) billing.

This module is the one place the price lives. $0.90 per started 30-second block, with a
half-second allowance so a 30.02 s ad is one block, and a minimum of one block. The pages get the
price from GET /api/limits; they hold no copy of it.
"""

import math

CENTS_PER_BLOCK = 90
BLOCK_SECONDS = 30
ALLOWANCE_SECONDS = 0.5


def estimate_cost_cents(duration_seconds):
    return CENTS_PER_BLOCK * max(
        1, math.ceil((duration_seconds - ALLOWANCE_SECONDS) / BLOCK_SECONDS)
    )


def estimate_cost_usd(duration_seconds):
    """For display only."""
    return estimate_cost_cents(duration_seconds) / 100
