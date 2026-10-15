"""Render the SVG app icons to PNG with headless Chromium (playwright).

Run once after changing static/icons/*.svg: python tools/make_icons.py
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

ICONS = Path(__file__).resolve().parent.parent / "static" / "icons"
TARGETS = [
    ("icon.svg", "icon-192.png", 192),
    ("icon.svg", "icon-512.png", 512),
    ("maskable.svg", "icon-maskable-512.png", 512),
    ("maskable.svg", "apple-touch-icon.png", 180),
]

with sync_playwright() as p:
    browser = p.chromium.launch()
    for src, dest, size in TARGETS:
        page = browser.new_page(viewport={"width": size, "height": size})
        svg = (ICONS / src).read_text().replace("<svg ", f"<svg width={size} height={size} ", 1)
        page.set_content(
            f'<html><body style="margin:0;background:transparent">'
            f'<div style="width:{size}px;height:{size}px">{svg}</div>'
            f"</body></html>"
        )
        page.screenshot(
            path=str(ICONS / dest), omit_background=True, clip={"x": 0, "y": 0, "width": size, "height": size}
        )
        page.close()
        print("wrote", dest)
    browser.close()
