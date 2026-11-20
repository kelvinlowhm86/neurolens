"""The pages hold no price of their own: they take it from GET /api/limits (limits.price)."""

from pathlib import Path

import pytest
from conftest import CFG
from neurolens import settings

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGES = [REPO_ROOT / "static" / "app.html", REPO_ROOT / "static" / "index.html"]


@pytest.mark.parametrize("page", PAGES, ids=lambda path: path.name)
def test_page_holds_no_hard_coded_price(page):
    text = page.read_text()
    cents = settings.cents_per_block(CFG)
    for needle in (
        f"{cents} * Math.max",
        f"dollars({cents})",
        f"${cents / 100:.2f}",
    ):
        assert needle not in text, f"{page.name} hard-codes the price: {needle!r}"


def test_app_reads_the_price_from_the_limits():
    # The landing page shows no price at all (non-commercial service).
    assert "cents_per_block" in (REPO_ROOT / "static" / "app.html").read_text()
