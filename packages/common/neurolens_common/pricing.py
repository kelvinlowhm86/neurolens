"""Credit pricing. A 30 s ad costs 30 credits ≈ $0.90 at the starter-pack rate."""

import math

from .config import CreditPack, Settings, get_settings


def credits_for_duration(seconds: float, cfg: Settings | None = None) -> int:
    cfg = cfg or get_settings()
    return max(cfg.min_charge_credits, math.ceil(seconds * cfg.credits_per_second))


def find_pack(pack_id: str, cfg: Settings | None = None) -> CreditPack | None:
    cfg = cfg or get_settings()
    return next((p for p in cfg.credit_packs if p.id == pack_id), None)
