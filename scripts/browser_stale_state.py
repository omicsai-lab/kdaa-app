"""Regression checks for stale asynchronous responses in the Web client.

Runs a real browser against an already running app and intercepts the API to control timing,
which the live synchronous backend cannot express on its own:

  A. A run poll that is still in flight when the user switches workspace must be discarded.
     Its reply must not install the previous workspace's run, provenance or error.
  B. When a poll observes a terminal status, the run's history summary and provenance must
     refresh, while the selected workspace, selected run and active tab stay untouched.

Only responses are rewritten; no application state, rule or boundary is bypassed.

    .venv-browser/bin/python scripts/browser_stale_state.py --base http://127.0.0.1:5183
"""
import argparse
import asyncio
import json
import os
import re
from urllib.parse import urlparse
from uuid import uuid4

from playwright.async_api import async_playwright, expect

RUN = re.compile(r"^/api/v1/runs/[0-9a-fA-F-]{36}$")
RUN_PROVENANCE = re.compile(r"^/api/v1/runs/([0-9a-fA-F-]{36})/provenance$")
WORKSPACE_RUNS = re.compile(r"^/api/v1/workspaces/[0-9a-fA-F-]{36}/runs$")


class Control:
    """Pretends the selected run is still executing, and can hold a poll open on demand."""

    def __init__(self):
        self.pretend_running = False
        self.truncate_provenance = False
        self.hold_next_poll = False
        self.poll_held = asyncio.Event()
        self.release_poll = asyncio.Event()
        self.polls = 0

    async def handle(self, route):
        path = urlparse(route.request.url).path
        if route.request.method != "GET":
            await route.continue_()
            return
        if RUN.match(path):
            await self.poll(route)
        elif WORKSPACE_RUNS.match(path) and self.pretend_running:
            await self.history(route)
        elif RUN_PROVENANCE.match(path) and self.truncate_provenance:
            await self.provenance(route)
        else:
            await route.continue_()

    async def body(self, route):
        response = await route.fetch()
        return await response.json()

    async def fulfill(self, route, payload):
        await route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    async def poll(self, route):
        self.polls += 1
        payload = await self.body(route)
        if self.pretend_running:
            payload["status"] = "running"
            payload["result"] = None
        if self.hold_next_poll:
            self.hold_next_poll = False
            self.poll_held.set()
            await self.release_poll.wait()          # keep this reply in flight across the switch
        await self.fulfill(route, payload)

    async def history(self, route):
        payload = await self.body(route)
        for summary in payload:
            summary["status"] = "running"
            summary["candidate_count"] = None
        await self.fulfill(route, payload)

    async def provenance(self, route):
        payload = await self.body(route)
        await self.fulfill(route, payload[:1])      # a shorter trail, so a refresh is observable


async def new_workspace(page, name, goal=""):
    await page.get_by_role("button", name="New workspace", exact=True).click()
    await page.get_by_label("Workspace name", exact=True).fill(name)
    await page.get_by_label("What could this material help you do?").fill(goal)
    await page.get_by_role("button", name="Create workspace", exact=True).click()


async def selected_run_option(page):
    return await page.get_by_label("Run history").evaluate(
        "el => el.selectedIndex >= 0 ? el.options[el.selectedIndex].textContent : ''")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5183")
    args = parser.parse_args()
    suffix = uuid4().hex[:6]
    name_a, name_b = f"Stale guard A {suffix}", f"Stale guard B {suffix}"
    control = Control()
    page_errors = []
    results = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=os.getenv("CHROMIUM_EXECUTABLE") or None)
        page = await browser.new_page(viewport={"width": 1440, "height": 1050})
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        await page.route("**/api/v1/**", control.handle)
        await page.goto(args.base)

        # Workspace A gets a real completed run; workspace B stays empty.
        await new_workspace(page, name_a, "Reuse teaching and software assets.")
        await page.get_by_role("button", name="Load demo", exact=True).click()
        await expect(page.get_by_text("01-copperfin-software.md", exact=True)).to_be_visible(timeout=30000)
        await page.get_by_role("button", name="Run KDAA", exact=True).click()
        await expect(page.get_by_role("heading", name="Candidate knowledge assets", exact=True)).to_be_visible(timeout=60000)
        real_run_option = await selected_run_option(page)
        assert "succeeded" in real_run_option, real_run_option
        await new_workspace(page, name_b)
        await expect(page.get_by_role("heading", name="No source documents yet", exact=True)).to_be_visible(timeout=30000)

        # --- A. A poll still in flight when the workspace changes must be discarded ---------
        control.pretend_running = True
        await page.get_by_role("button", name=name_a, exact=True).click()
        await expect(page.get_by_label("Run history")).to_be_enabled(timeout=30000)
        assert "running" in await selected_run_option(page), "The run should appear as running while intercepted"
        control.hold_next_poll = True
        await asyncio.wait_for(control.poll_held.wait(), timeout=30)   # a poll for workspace A is now pending

        await page.get_by_role("button", name=name_b, exact=True).click()
        await expect(page.get_by_role("heading", name="No source documents yet", exact=True)).to_be_visible(timeout=30000)
        control.pretend_running = False
        control.release_poll.set()                                     # the stale reply lands now
        await page.wait_for_timeout(2500)

        assert await selected_run_option(page) in ("", "No runs yet"), \
            f"A stale poll installed a previous workspace's run: {await selected_run_option(page)!r}"
        await expect(page.locator(".alert.error")).to_have_count(0)
        await page.get_by_role("tab", name="Discovery", exact=True).click()
        await expect(page.get_by_role("heading", name="Start with a reference run", exact=True)).to_be_visible()
        await expect(page.locator(".candidate-card")).to_have_count(0)
        await expect(page.get_by_role("heading", name=name_b, exact=True)).to_be_visible()
        results["stale_poll_discarded_on_workspace_switch"] = True

        # --- B. Reaching a terminal status refreshes history and provenance, not the selection ---
        control.pretend_running = True
        control.truncate_provenance = True
        control.release_poll.clear()
        control.poll_held.clear()
        await page.get_by_role("button", name=name_a, exact=True).click()
        await expect(page.get_by_label("Run history")).to_be_enabled(timeout=30000)
        assert "running" in await selected_run_option(page)
        await page.get_by_role("tab", name="Provenance", exact=True).click()
        await expect(page.locator(".event-row")).to_have_count(1, timeout=30000)
        before = {"option": await selected_run_option(page), "events": await page.locator(".event-row").count()}

        control.pretend_running = False
        control.truncate_provenance = False
        polls_before = control.polls
        await expect(page.locator(".event-row")).not_to_have_count(1, timeout=30000)

        after_option = await selected_run_option(page)
        after_events = await page.locator(".event-row").count()
        assert "succeeded" in after_option, f"History summary was not refreshed on completion: {after_option!r}"
        assert after_events > before["events"], "Provenance was not refreshed on completion"
        assert before["option"].split("·")[-1].strip() == after_option.split("·")[-1].strip(), \
            "Polling changed which run is selected"
        await expect(page.get_by_role("tab", name="Provenance", exact=True)).to_have_attribute("aria-selected", "true")
        await expect(page.get_by_role("heading", name=name_a, exact=True)).to_be_visible()
        await expect(page.locator(".alert.error")).to_have_count(0)
        results["terminal_poll_refreshes_history_and_provenance"] = {
            "polls_observed": control.polls - polls_before,
            "events_before": before["events"], "events_after": after_events,
            "status_before": "running", "status_after": "succeeded",
            "selection_unchanged": True, "tab_unchanged": "provenance"}

        assert not page_errors, page_errors
        await browser.close()
    print(json.dumps({"passed": True, **results}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
