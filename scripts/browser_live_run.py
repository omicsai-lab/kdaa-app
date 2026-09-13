"""One real live Bedrock workflow, driven through the local Web UI.

Unlike `browser_live_ui.py`, this script intercepts nothing: the client talks to the running API,
which calls the configured Bedrock model. **It makes billable model calls**, so it requires
`--allow-paid-call` and performs exactly one run.

    .venv-browser/bin/python scripts/browser_live_run.py --base http://127.0.0.1:5183 \
        --allow-paid-call --screenshots docs/screenshots/live

It verifies discovery, the semantic assessment, the drafted artifact, exact quotation highlighting,
provenance, both exports and a saved-run reload, then prints the model id, region, token usage,
elapsed time and any rejected quotations for the build ledger. It creates its own clearly named
workspace and never edits existing data.
"""
import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright, expect

DEMO_FIRST = "01-copperfin-software.md"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5183")
    parser.add_argument("--allow-paid-call", action="store_true",
                        help="Required. Without it the script refuses to run, so a live model call "
                             "is never made by accident.")
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--timeout-ms", type=int, default=300_000)
    parser.add_argument("--verify-workspace", metavar="NAME",
                        help="Verify the saved run in this existing workspace instead of starting "
                             "a new one. Makes no model call, so --allow-paid-call is not needed.")
    args = parser.parse_args()
    if not args.allow_paid_call and not args.verify_workspace:
        raise SystemExit("Refusing to run: live mode makes billable Bedrock calls. "
                         "Pass --allow-paid-call to proceed, or --verify-workspace NAME to "
                         "re-check a run that already exists.")

    errors, results = [], {}
    with sync_playwright() as p, tempfile.TemporaryDirectory() as temporary:
        browser = p.chromium.launch(headless=True,
                                    executable_path=os.getenv("CHROMIUM_EXECUTABLE") or None)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.base)

        # The server must already advertise live mode; this script does not fake that.
        expect(page.get_by_text("No cloud connection", exact=True)).to_have_count(0)
        pill = page.locator(".local-pill").first
        expect(pill).to_be_visible(timeout=30_000)
        results["connection_label"] = pill.inner_text().strip()
        assert "Bedrock" in results["connection_label"], results["connection_label"]

        if args.verify_workspace:
            # Re-check an existing saved run. No workspace is created and no run is started.
            name = args.verify_workspace
            page.get_by_role("button", name=name, exact=True).click()
            expect(page.get_by_role("heading", name=name, exact=True)).to_be_visible(timeout=30_000)
            # A freshly loaded workspace opens on Sources; the run's results live under Discovery.
            page.get_by_role("tab", name="Discovery", exact=True).click()
            expect(page.get_by_role("heading", name="Candidate knowledge assets", exact=True)
                   ).to_be_visible(timeout=30_000)
            results["verified_saved_run_only"] = True
        else:
            name = f"Live Bedrock acceptance {uuid4().hex[:6]}"
            page.get_by_role("button", name="New workspace", exact=True).click()
            page.get_by_label("Workspace name", exact=True).fill(name)
            page.get_by_label("What could this material help you do?").fill(
                "Develop a teaching exercise from software and field notes.")
            page.get_by_role("button", name="Create workspace", exact=True).click()

            # Small demo input: three short Markdown sources, well under the input bound.
            page.get_by_role("button", name="Load demo", exact=True).click()
            expect(page.get_by_text(DEMO_FIRST, exact=True)).to_be_visible(timeout=30_000)

            selector = page.get_by_role("radiogroup", name="Analysis engine")
            live_option = selector.get_by_role("radio", name="Live AI")
            expect(live_option).to_be_enabled()
            live_option.click()
            expect(live_option).to_have_attribute("aria-checked", "true")

            # ---- the one paid run ------------------------------------------------------------
            started = time.monotonic()
            page.get_by_role("button", name="Run live AI", exact=True).click()
            expect(page.get_by_role("heading", name="Candidate knowledge assets", exact=True)
                   ).to_be_visible(timeout=args.timeout_ms)
            results["elapsed_seconds_ui"] = round(time.monotonic() - started, 1)
        expect(page.get_by_text("Live AI mode", exact=True)).to_be_visible()
        cards = page.locator(".candidate-card")
        results["candidates_rendered"] = cards.count()
        assert results["candidates_rendered"] > 0, "the live run produced no candidates"
        if args.screenshots:
            args.screenshots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.screenshots / "live-discovery.png"), full_page=True)

        # Exact quotation: the inspector must confirm the quote against the stored source.
        page.get_by_role("button", name="Inspect evidence 1 for").first.click()
        dialog = page.get_by_role("dialog", name="Evidence inspector")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_text("Exact quote verified", exact=True)).to_be_visible()
        results["exact_quote_verified"] = True
        if args.screenshots:
            page.screenshot(path=str(args.screenshots / "live-evidence.png"))
        page.get_by_role("button", name="Close dialog", exact=True).click()

        page.get_by_role("tab", name="Assessment", exact=True).click()
        expect(page.get_by_text("a model reading, not a score", exact=True).first).to_be_visible()
        expect(page.get_by_text("Possible uses", exact=True).first).to_be_visible()
        results["semantic_assessment_rendered"] = True

        page.get_by_role("tab", name="Amplification", exact=True).click()
        draft = page.locator(".draft").first
        expect(draft).to_be_visible()
        expect(draft.get_by_text("GENERATED DRAFT", exact=True)).to_be_visible()
        body = draft.locator(".draft-body").inner_text()
        assert body.strip(), "the drafted artifact is empty"
        grounding = draft.locator("p.subtle").last.inner_text()
        results["draft"] = {"characters": len(body), "grounding_line": grounding[:160]}
        expect(page.get_by_text("Human verification gate", exact=True).first).to_be_visible()
        if args.screenshots:
            page.screenshot(path=str(args.screenshots / "live-draft.png"), full_page=True)

        # Exports. The JSON bundle carries the provider record, so read the real numbers from it.
        with page.expect_download() as markdown_event:
            page.get_by_role("button", name="Export Markdown", exact=True).click()
        markdown_path = Path(temporary) / "run.md"
        markdown_event.value.save_as(markdown_path)
        markdown = markdown_path.read_text()
        assert markdown.lstrip().startswith("#"), "Markdown export is not a Markdown document"
        assert DEMO_FIRST in markdown, "Markdown export does not cite the demo source"

        with page.expect_download() as json_event:
            page.get_by_role("button", name="Export JSON", exact=True).click()
        json_path = Path(temporary) / "run.json"
        json_event.value.save_as(json_path)
        exported = json.loads(json_path.read_text())
        run = exported["run"]
        bundle, provider = run["result"], run["result"]["provider"]
        assert provider is not None and provider["simulated"] is False, \
            "the exported run is not a real live run"
        results["exports"] = {"markdown_bytes": len(markdown.encode()),
                              "json_sources": len(exported["sources"])}
        results["run_id"] = run["id"]
        results["model"] = {"provider": provider["provider"], "model": provider["model"],
                            "region": provider["region"], "calls": provider["calls"],
                            "usage": provider["usage"],
                            "prompt_versions": provider["prompt_versions"]}
        results["per_call"] = [{"stage": i["stage"], "usage": i["usage"],
                                "latency_ms": i["latency_ms"], "stop_reason": i["stop_reason"]}
                               for i in provider["interactions"]]
        results["latency_ms_total"] = sum(i["latency_ms"] for i in provider["interactions"])
        results["rejections"] = [{"stage": r["stage"], "reason": r["reason"],
                                  "candidate_title": r["candidate_title"], "detail": r["detail"]}
                                 for r in bundle["rejections"]]
        results["draft_grounding"] = [
            {"candidate_id": a["candidate_id"],
             "grounded_evidence_ids": a["draft"]["grounded_evidence_ids"],
             "unresolved_evidence_refs": a["draft"]["unresolved_evidence_refs"]}
            for a in bundle["amplifications"] if a["draft"]]
        actions = [e["action"] for e in exported["provenance"]]
        results["provenance"] = {"events": len(actions),
                                 "llm_calls": actions.count("llm.call"),
                                 "evidence_rejected": actions.count("evidence.rejected"),
                                 "draft_grounding_unresolved": actions.count("draft.grounding_unresolved"),
                                 "terminal": actions[-1]}
        # Every model call must be individually accounted for in the ledger.
        assert results["provenance"]["llm_calls"] == provider["calls"], \
            f'{results["provenance"]["llm_calls"]} llm.call events vs {provider["calls"]} calls'

        # Saved-run reload: the stored bundle must come back from PostgreSQL unchanged.
        page.reload()
        expect(page.get_by_role("button", name=DEMO_FIRST, exact=True)).to_be_visible(timeout=30_000)
        page.get_by_role("tab", name="Provenance", exact=True).click()
        expect(page.get_by_text("run · succeeded", exact=True)).to_be_visible()
        page.get_by_role("tab", name="Discovery", exact=True).click()
        expect(page.locator(".candidate-card")).to_have_count(results["candidates_rendered"])
        expect(page.get_by_text("Live AI mode", exact=True)).to_be_visible()
        page.get_by_role("group").filter(
            has_text="Model, limitations and rejected quotations").locator("summary").click()
        record = page.locator(".limitations .subtle").first.inner_text()
        assert provider["model"] in record, record
        assert f'{provider["calls"]} model call(s)' in record, record
        results["saved_run_reload"] = {"model_record_after_reload": record[:200]}
        if args.screenshots:
            page.screenshot(path=str(args.screenshots / "live-provenance.png"), full_page=True)

        expect(page.locator(".alert.error")).to_have_count(0)
        assert not errors, errors
        browser.close()

    print(json.dumps({"passed": True, "live_bedrock_call": True, "workspace": name, **results},
                     indent=2))


if __name__ == "__main__":
    main()
