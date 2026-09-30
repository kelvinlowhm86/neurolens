"""The cost formula, shared by the web app, the worker and (from M3) billing.

$0.90 per started 30-second block, with a half-second allowance so a 30.02 s ad is one block,
and a minimum of one block.

NOTE: static/ mirrors this formula in JavaScript. Change both together.
"""

import math


def estimate_cost_cents(duration_seconds):
    return 90 * max(1, math.ceil((duration_seconds - 0.5) / 30))


def estimate_cost_usd(duration_seconds):
    """For display only."""
    return estimate_cost_cents(duration_seconds) / 100
