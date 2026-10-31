"""The pages hold no price of their own: they take it from GET /api/limits (limits.price)."""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGES = [REPO_ROOT / "static" / "app.html", REPO_ROOT / "static" / "index.html"]


@pytest.mark.parametrize("page", PAGES, ids=lambda path: path.name)
def test_page_holds_no_hard_coded_price(page):
    text = page.read_text()
    for needle in ("90 *", "dollars(90)", "$0.90", "0.90"):
        assert needle not in text, f"{page.name} hard-codes the price: {needle!r}"


@pytest.mark.parametrize("page", PAGES, ids=lambda path: path.name)
def test_page_reads_the_price_from_the_limits(page):
    assert "cents_per_block" in page.read_text()
