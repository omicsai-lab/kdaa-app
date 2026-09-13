"""Optional real browser acceptance against an already running app.

Install Playwright in an isolated environment, install its Chromium browser, then:
    python scripts/browser_smoke.py --base http://127.0.0.1:5183
Set CHROMIUM_EXECUTABLE only to use an already installed compatible browser.
Does not replace PostgreSQL/Compose tests. Never deletes app data.
"""
import argparse
import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4
from playwright.sync_api import sync_playwright, expect

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5183")
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    errors = []
    with sync_playwright() as p, tempfile.TemporaryDirectory() as temporary:
        browser = p.chromium.launch(headless=True, executable_path=os.getenv("CHROMIUM_EXECUTABLE") or None)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.base)
        page.get_by_role("button", name="New workspace", exact=True).click()
        page.get_by_label("Workspace name", exact=True).fill(f"Browser acceptance {uuid4().hex[:6]}")
        page.get_by_label("What could this material help you do?").fill("Develop a teaching exercise from software and field notes.")
        page.get_by_role("button", name="Create workspace", exact=True).click()
        page.get_by_role("button", name="Load demo", exact=True).click()
        expect(page.get_by_text("01-copperfin-software.md", exact=True)).to_be_visible(timeout=30000)
        page.get_by_role("button", name="Run KDAA", exact=True).click()
        expect(page.get_by_role("heading", name="Candidate knowledge assets", exact=True)).to_be_visible(timeout=30000)
        expect(page.locator(".candidate-card")).to_have_count(5)
        if args.screenshots:
            args.screenshots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.screenshots / "discovery.png"), full_page=True)
        page.get_by_role("button", name="Inspect evidence 1 for Software:").first.click()
        dialog = page.get_by_role("dialog", name="Evidence inspector")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_text("Exact quote verified", exact=True)).to_be_visible()
        if args.screenshots: page.screenshot(path=str(args.screenshots / "evidence.png"))
        page.get_by_role("button", name="Close dialog", exact=True).click()
        page.get_by_role("tab", name="Assessment", exact=True).click()
        expect(page.get_by_text("Source traceability", exact=True).first).to_be_visible()
        page.get_by_role("tab", name="Amplification", exact=True).click()
        expect(page.get_by_text("Human verification gate", exact=True).first).to_be_visible()
        with page.expect_download() as download_event:
            page.get_by_role("button", name="Export JSON", exact=True).click()
        target = Path(temporary) / "run.json"
        download_event.value.save_as(target)
        exported = json.loads(target.read_text())
        assert len(exported["sources"]) == 3
        page.reload()
        expect(page.get_by_role("button", name="01-copperfin-software.md", exact=True)).to_be_visible()
        page.get_by_role("tab", name="Provenance", exact=True).click()
        expect(page.get_by_text("run · succeeded", exact=True)).to_be_visible()
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("tab", name="Sources").click()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "Horizontal overflow on mobile"
        if args.screenshots: page.screenshot(path=str(args.screenshots / "mobile.png"), full_page=True)
        assert not errors, errors
        browser.close()
    print("Browser acceptance passed: workspace, demo import, run, evidence, assessment, amplification, export, refresh, mobile layout.")

if __name__ == "__main__":
    main()
