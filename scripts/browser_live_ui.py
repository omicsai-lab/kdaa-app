"""Live-mode UI rendering check against the running app.

Intercepts the API so the client sees a live-capable server and a schema-accurate live run
(produced by scripts/make_live_ui_fixture.py from the real LiveEngine). It verifies that the mode
selector, the connection label, the generated draft, the proposed-content markers, the rejected
quotations and the model record all render.

This exercises the UI only. It makes no Bedrock call and is NOT evidence that live inference works
against a real model; that requires configured credentials and a separate live check.

    .venv-browser/bin/python scripts/browser_live_ui.py --base http://127.0.0.1:5183 \
        --fixture .fixtures/live-run.json
"""
import argparse
import asyncio
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from playwright.async_api import async_playwright, expect

MODEL = "us.anthropic.example-model-v1:0"
RUNS = re.compile(r"^/api/v1/workspaces/([0-9a-fA-F-]{36})/runs$")
RUN = re.compile(r"^/api/v1/runs/([0-9a-fA-F-]{36})$")
PROVENANCE = re.compile(r"^/api/v1/runs/([0-9a-fA-F-]{36})/provenance$")

class Api:
    def __init__(self, run: dict):
        self.run = run
        self.summary = {"id": run["id"], "workspace_id": run["workspace_id"], "status": run["status"],
                        "created_at": run["created_at"], "updated_at": run["updated_at"],
                        "engine_version": run["engine_version"],
                        "candidate_count": len(run["result"]["hypotheses"]), "error": None}
        self.started = False

    async def handle(self, route):
        request = route.request
        path = urlparse(request.url).path
        if path == "/api/v1/system":
            payload = await (await route.fetch()).json()
            payload |= {"modes": ["live", "reference"], "live_llm": True,
                        "llm": payload["llm"] | {"enabled": True, "provider": "aws-bedrock-converse",
                                                 "model": MODEL, "region": "us-east-1"}}
            return await self._json(route, payload)
        if RUNS.match(path) and request.method == "POST":
            body = json.loads(request.post_data or "{}")
            assert body.get("mode") == "live", f"client did not request live mode: {body}"
            self.started = True
            return await self._json(route, self.run, status=201)
        if RUNS.match(path) and request.method == "GET":
            return await self._json(route, [self.summary] if self.started else [])
        if (RUN.match(path) or PROVENANCE.match(path)) and RUN.match(path) and path.endswith(self.run["id"]):
            return await self._json(route, self.run)
        if PROVENANCE.match(path) and PROVENANCE.match(path).group(1) == self.run["id"]:
            return await self._json(route, [])
        await route.continue_()

    async def _json(self, route, payload, status=200):
        await route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5183")
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    run = json.loads(args.fixture.read_text())
    api, errors, results = Api(run), [], {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=os.getenv("CHROMIUM_EXECUTABLE") or None)
        page = await browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.route("**/api/v1/**", api.handle)
        await page.goto(args.base)

        # The stale "No cloud connection" label must be replaced when live mode is configured.
        await expect(page.get_by_text(f"Bedrock · {MODEL}", exact=True)).to_be_visible(timeout=30000)
        await expect(page.get_by_text("No cloud connection", exact=True)).to_have_count(0)
        await expect(page.get_by_text("LOCAL + LIVE AI EDITION", exact=True)).to_be_visible()
        results["connection_label_updated"] = True

        await page.get_by_role("button", name="New workspace", exact=True).click()
        await page.get_by_label("Workspace name", exact=True).fill(f"Live UI {uuid4().hex[:6]}")
        await page.get_by_label("What could this material help you do?").fill("Reuse tooling for field notes.")
        await page.get_by_role("button", name="Create workspace", exact=True).click()
        await expect(page.get_by_role("button", name="Load demo", exact=True)).to_be_enabled(timeout=30000)

        selector = page.get_by_role("radiogroup", name="Analysis engine")
        await expect(selector.get_by_role("radio", name="Reference")).to_be_visible()
        live_option = selector.get_by_role("radio", name="Live AI")
        await expect(live_option).to_be_enabled()
        await live_option.click()
        await expect(live_option).to_have_attribute("aria-checked", "true")
        await expect(page.get_by_role("button", name="Run live AI", exact=True)).to_be_visible()
        results["mode_selector"] = True

        await page.locator("input[type=file]").set_input_files(
            {"name": "copperfin.txt", "mimeType": "text/plain",
             "buffer": b"Copperfin field toolkit\n\nThe Copperfin utility cleans messy field notes.\n"})
        await expect(page.get_by_role("button", name="copperfin.txt", exact=True)).to_be_visible(timeout=30000)
        await page.get_by_role("button", name="Run live AI", exact=True).click()
        await expect(page.get_by_role("heading", name="Candidate knowledge assets", exact=True)).to_be_visible(timeout=60000)
        await expect(page.get_by_text("Live AI mode", exact=True)).to_be_visible()
        await expect(page.locator(".candidate-card")).to_have_count(1)
        results["live_run_rendered"] = True
        if args.screenshots:
            args.screenshots.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(args.screenshots / "live-discovery.png"), full_page=True)

        # Assessment shows the model's explanation, not a repurposed lexical verdict or a score.
        await page.get_by_role("tab", name="Assessment", exact=True).click()
        await expect(page.get_by_text("a model reading, not a score", exact=True)).to_be_visible()
        await expect(page.get_by_text("Possible uses", exact=True)).to_be_visible()
        await expect(page.get_by_text("Limitations", exact=True).first).to_be_visible()
        results["semantic_assessment_rendered"] = True

        # Amplification shows the actual drafted artifact and what in it is only proposed.
        await page.get_by_role("tab", name="Amplification", exact=True).click()
        draft = page.locator(".draft").first
        await expect(draft).to_be_visible()
        await expect(draft.get_by_text("GENERATED DRAFT", exact=True)).to_be_visible()
        body = await draft.locator(".draft-body").inner_text()
        assert "## Getting started" in body and "Cleans messy field notes" in body, body[:200]
        await expect(draft.get_by_text("Proposed, not supported by the quoted evidence", exact=True)).to_be_visible()
        await expect(page.get_by_text("Human verification gate", exact=True).first).to_be_visible()
        results["draft_rendered"] = {"characters": len(body)}
        if args.screenshots:
            await page.screenshot(path=str(args.screenshots / "live-draft.png"), full_page=True)

        # Rejected model quotations and the model record are both inspectable.
        await page.get_by_role("tab", name="Discovery", exact=True).click()
        await page.get_by_role("group").filter(has_text="Model, limitations and rejected quotations").locator("summary").click()
        rejections = page.locator(".rejections li")
        await expect(rejections.first).to_be_visible()
        count = await rejections.count()
        assert count == len(run["result"]["rejections"]), f"{count} shown, {len(run['result']['rejections'])} stored"
        record = await page.locator(".limitations .subtle").first.inner_text()
        stored = run["result"]["provider"]
        assert "Produced by" in record and stored["model"] in record, record
        assert f"{stored['calls']} model call(s)" in record, record
        results["rejections_and_model_record"] = {"rejections_shown": count,
                                                  "provider_recorded": stored["provider"],
                                                  "model_recorded": stored["model"]}

        # A saved reference run must keep its own labelling even while Live AI is selected.
        await expect(page.locator(".alert.error")).to_have_count(0)
        assert not errors, errors
        await browser.close()
    print(json.dumps({"passed": True, "note": "UI rendering only; no Bedrock call was made.", **results}, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
