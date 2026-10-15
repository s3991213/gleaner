"""Headless browser check of the running app (Definition of done, step 6).

Usage: python tests/e2e_browser.py [base_url] [screenshot_dir]
Needs: pip install playwright && playwright install chromium. Uses the network.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
SHOTS = Path(sys.argv[2]) if len(sys.argv) > 2 else None


def shot(page, name: str) -> None:
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"{name}.png"))


def main() -> int:
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = context.new_page()
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        page.goto(BASE, wait_until="networkidle")
        tutorial = page.locator("#tutorial")
        expect(tutorial).to_be_visible()
        expect(page.locator("#tutorial-title")).to_have_text("Welcome to Gleaner")
        print("PASS tutorial shows on first load")
        shot(page, "1-tutorial")

        page.click("#tutorial-skip")
        expect(tutorial).to_be_hidden()
        print("PASS tutorial can be skipped")

        page.reload(wait_until="networkidle")
        page.wait_for_timeout(500)
        expect(tutorial).to_be_hidden()
        print("PASS tutorial does not show on reload")

        page.click("#tab-discover")
        add = page.get_by_role("button", name="Add Hacker News front page")
        expect(add).to_be_visible()
        shot(page, "2-discover")
        add.click()
        page.click("#tab-feed")
        items = page.locator("#item-list .item")
        expect(items.first).to_be_visible(timeout=20000)
        count = items.count()
        assert count > 0
        print(f"PASS adding a starter feed shows items ({count} items)")
        shot(page, "3-feed")

        width = page.evaluate("document.documentElement.scrollWidth")
        assert width <= 390, f"horizontal overflow: {width}px"
        print("PASS no horizontal overflow at 390px")

        # Tutorial reopens from the help button.
        page.click("#help-btn")
        expect(tutorial).to_be_visible()
        page.keyboard.press("Escape")
        expect(tutorial).to_be_hidden()
        print("PASS tutorial reopens from ? button")

        browser.close()

    if errors:
        print("FAIL page errors:", *errors, sep="\n  ")
        return 1
    print("All browser checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
