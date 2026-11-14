#!/usr/bin/env python3
"""Capture high-resolution element stills from the live NeuroLens site for the walkthrough.

Read-only: opens public sample results only. Never signs in, uploads or starts a job.
Uses the installed Google Chrome (channel="chrome"); never run `playwright install`.

    python tools/capture.py            # writes public/walkthrough/captures/*.png
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

SITE = "https://df6girqyykyfa.cloudfront.net"
OUT = Path(__file__).resolve().parents[1] / "public" / "walkthrough" / "captures"
SCALE = 3
VIEWPORT = {"width": 1470, "height": 900}
# Text-heavy panels: a narrower page wraps them into compact blocks that read large on screen.
NARROW = {"width": 860, "height": 1100}

# Hide page chrome that must never appear in a crop.
HIDE_CSS = """
header, nav, .topbar, .site-header, #userEmail, .user-email, .sidebar, #sidebar { visibility: hidden !important; }
*, *::before, *::after { transition: none !important; animation: none !important; caret-color: transparent !important; }
"""


def shot(page: Page, selector: str, name: str, nth: int = 0) -> None:
    loc = page.locator(selector).nth(nth)
    loc.wait_for(state="visible", timeout=15000)
    loc.scroll_into_view_if_needed()
    page.wait_for_timeout(600)
    path = OUT / f"{name}.png"
    loc.screenshot(path=str(path), animations="disabled")
    print(f"  {name}: {selector}[{nth}]")


def open_sample(page: Page, text: str, second: float) -> None:
    page.goto(SITE + "/app", wait_until="domcontentloaded")
    page.locator(".sample-card", has_text=text).first.wait_for(timeout=20000)
    page.locator(".sample-card", has_text=text).first.click()
    page.locator("#chartCard").wait_for(state="visible", timeout=20000)
    page.wait_for_timeout(4000)
    page.add_style_tag(content=HIDE_CSS)
    # Park the video (and its playhead) on a fixed second so every capture agrees.
    page.evaluate(
        "(t) => { const v = document.querySelector('#videoPlayer'); v.pause(); v.currentTime = t; }",
        second,
    )
    page.wait_for_timeout(1500)


def capture_home(page: Page) -> None:
    page.goto(SITE, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    page.add_style_tag(content=HIDE_CSS)
    shot(page, ".hero h1", "home_headline")


def capture_result(page: Page) -> None:
    open_sample(page, "Swee Choon", 10)
    shot(page, "#chartCard", "chart")
    shot(page, ".brain-panel", "brain")
    shot(page, "#videoBox", "video_frame")
    for i, name in enumerate(["strip_seeing", "strip_hearing", "strip_understanding"]):
        shot(page, "#stripsHost .strip", name, i)
    page.get_by_text("Show 4 parts").first.click()
    page.wait_for_timeout(1500)
    shot(page, "#stripsCard", "strips_open")

    open_sample(page, "Swee Choon", 10)
    mark = page.locator(".moment-mark").nth(1)
    mark.hover()
    page.wait_for_timeout(1200)
    shot(page, "#chartCard", "chart_moment")

    open_sample(page, "Swee Choon", 10)
    page.locator("#mutedToggle").click()
    page.wait_for_timeout(2000)
    shot(page, "#chartCard", "chart_muted")


def capture_report(page: Page) -> None:
    open_sample(page, "Swee Choon", 10)
    page.locator("#reportTabBtn").click()
    page.wait_for_timeout(3000)
    shot(page, "#reportAnswer :is(h2, h3):visible", "report_head")
    shot(page, ".report-lists", "report_lists")
    shot(page, ".report-moment", "report_moment_1", 1)
    shot(page, ".report-moment", "report_moment_2", 2)


def capture_compare(page: Page) -> None:
    open_sample(page, "CeraVe ad (30", 0)
    page.locator("#compareTabBtn").click()
    page.wait_for_timeout(2500)
    sel = page.locator("#compareSelect")
    sel.select_option(sel.locator("option", has_text="76").first.get_attribute("value"))
    page.wait_for_timeout(8000)
    shot(page, "#compareVerdict", "compare_verdict")
    shot(page, ".compare-chart-panel", "compare_chart")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=SCALE)
        narrow = browser.new_page(viewport=NARROW, device_scale_factor=SCALE)
        try:
            for step, target in ((capture_home, page), (capture_result, page),
                                 (capture_report, narrow), (capture_compare, narrow)):
                print(step.__name__)
                step(target)
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
