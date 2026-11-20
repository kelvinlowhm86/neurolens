"""The cost formula, shared by the web app, the worker and billing.

This module is the one place the formula lives. The price itself comes from config.json
(`pricing.cents_per_block` per started `pricing.block_seconds` block), with a half-second
allowance so a 30.02 s ad is one block, and a minimum of one block. The pages get the price from
GET /api/limits; they hold no copy of it.
"""

import math

from neurolens import settings

ALLOWANCE_SECONDS = 0.5


def estimate_cost_cents(cfg, duration_seconds):
    return settings.cents_per_block(cfg) * max(
        1,
        math.ceil((duration_seconds - ALLOWANCE_SECONDS) / settings.block_seconds(cfg)),
    )


def estimate_cost_usd(cfg, duration_seconds):
    """For display only."""
    return estimate_cost_cents(cfg, duration_seconds) / 100
