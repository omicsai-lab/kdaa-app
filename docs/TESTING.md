# Verification guide

## What was executed here

See `BUILD_STATUS.md` for the dated laptop results. On 2026-09-12 the container suite ran **64 passed, 0 skipped** against real PostgreSQL 17.11, and the Compose stack, browser acceptance and restart persistence were executed on macOS 15 / arm64. The earlier generator-environment outputs in `verification/` (61 passed, 3 PostgreSQL tests skipped, plus a component-rendering check through a TestClient bridge) remain as historical partial evidence, superseded by the laptop run.

## Container suite (required for laptop acceptance)

No host Python environment is needed:

```bash
docker compose -p kdaa-app-tests -f compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
docker compose -p kdaa-app-tests -f compose.test.yml down
```

This separate Compose file starts PostgreSQL in tmpfs and builds the backend test target. It has no application volumes/ports. `KDAA_REQUIRE_POSTGRES_TESTS=1` makes a missing PostgreSQL test URL a test configuration error. A real configured driver/server failure must fail, not become a skip.

Real-PG tests require a dedicated database name ending `_test`; each test creates a random `kdaa_test_<UUIDhex>` schema and removes only that schema afterwards. They cover fresh migrations/metadata agreement, persistence across a new connection and interrupted-run recovery. Do not set the test URL to an existing research database.

Core/API tests use a test-only transactional store for fast fault injection and rollback assertions. The running application cannot select this adapter. Tests also exercise actual PDF/DOCX parsing, exact evidence integrity, duplicate support handling, source immutability, invalid uploads, partial-failure behavior and exported sources.

## Optional fresh host Python environment

Use Python 3.12 or 3.13, without touching other project environments:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock setuptools==82.0.1
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv/bin/python -m pytest -q
```

A host suite without a real PostgreSQL URL will skip the three integration tests; it is not the completion gate. Prefer the container suite for full acceptance.

## Web build

`apps/web/package-lock.json` is present, so a clean install is reproducible:

```bash
cd apps/web
npm ci
npm run build
```

`build` runs a strict TypeScript check and Vite's production build. Transpilation by itself is not type checking. Never replace this command with a syntax-only check to get a passing result.

## Real HTTP and restart persistence

With `docker compose up --build -d` already running:

```bash
python3 scripts/smoke_api.py --base http://127.0.0.1:8183 --restart
```

It creates one clearly labeled workspace, imports actual source fixtures, invokes the pipeline, checks evidence spans against exported normalized sources, exports both formats, and verifies unchanged saved results/source content after restarting only the backend. It leaves the created test workspace visible for inspection. It does not delete data.

A normal full `docker compose down` followed by `up -d` is an additional manual persistence check; never add `-v`. The browser should recover saved workspace/run state after refresh.

## Real browser acceptance

A separate Playwright environment is optional, so browser tooling does not inflate the runtime:

```bash
python3 -m venv .venv-browser
.venv-browser/bin/python -m pip install playwright
.venv-browser/bin/python -m playwright install chromium
.venv-browser/bin/python scripts/browser_smoke.py --base http://127.0.0.1:5183 --screenshots docs/screenshots/local
```

Record the installed Playwright version in `BUILD_STATUS.md`. Do not disable browser/OS security policy if automation is blocked; perform the documented flow manually and report the automated check as blocked. `CHROMIUM_EXECUTABLE` may point to an already installed compatible browser.

`browser_smoke.py` covers the demo path: workspace creation, demo import, run, evidence inspection, assessment, amplification, JSON export, refresh, provenance and a 390-pixel layout.

### Upload matrix and edge cases

`browser_uploads.py` covers the remaining required acceptance: a real text-based PDF and DOCX, the Markdown export, a duplicate source that adds no independent support, an irrelevant TXT that yields no candidates, and malformed PDF/DOCX uploads that must show a readable error while the API stays ready. Generate its fixtures with the pinned parser libraries first:

```bash
mkdir -p .fixtures
docker compose run --rm --no-deps --user root \
    -v "$PWD/scripts/make_upload_fixtures.py":/tmp/make_upload_fixtures.py \
    -v "$PWD/.fixtures":/out backend python /tmp/make_upload_fixtures.py --out /out
.venv-browser/bin/python scripts/browser_uploads.py --base http://127.0.0.1:5183 \
    --fixtures .fixtures --screenshots docs/screenshots/local
```

The fixtures are disposable generated input, not source material; `.fixtures/` is ignored by Git.

### Stale asynchronous state

`browser_stale_state.py` covers client-side request ordering, which the live synchronous backend cannot
express on its own. It intercepts API responses to hold a run poll open and to report a run as still
running, then checks two regressions:

```bash
.venv-browser/bin/python scripts/browser_stale_state.py --base http://127.0.0.1:5183
```

1. A poll still in flight when the user switches workspace is discarded, so the previous workspace's run,
   provenance and errors never appear in the new one.
2. When a poll observes a terminal status, that run's history summary and provenance refresh, while the
   selected workspace, selected run and active tab stay unchanged.

Only responses are rewritten. No application state, rule, bound or boundary is bypassed, and the script
creates its own clearly named workspaces rather than editing existing data.

Manual equivalent: create workspace; load demo; run; inspect source quotation; inspect assessment and human verification gate; export JSON and Markdown; refresh; inspect history/provenance; test a narrow screen. Also try one real text-based PDF, one DOCX, an irrelevant TXT, a duplicate and a malformed/unsupported upload. Errors must be visible and sources/results must survive a backend restart.

## Live AI mode

Everything about live mode is tested with **provider fixtures**: `tests/test_llm_engine.py` drives the
real `LiveEngine` through `StubLLMProvider`, and `tests/test_bedrock_adapter.py` drives the Bedrock
adapter through a fake client. Both run in the ordinary container suite with no network, no
credentials and no cost. They cover unfounded, ambiguous, oversized, empty and cross-document
quotations; malformed and non-JSON responses; an incomplete assessment; provider timeouts, denied
access and missing credentials; the input-size and model-call budgets; truncated (`max_tokens`)
output; and that a failed live run never becomes a reference result.

`tests/test_legacy_runs.py` pins backward compatibility against a stored schema-1.0 bundle: it must
still parse, round-trip, export, and show `provider: null` with no semantic or draft fields invented.

**Passing these proves nothing about a real Bedrock endpoint.** They exercise the Core's handling of
model output and the adapter's request shaping and failure translation. A live check needs configured
credentials and model access, and is recorded separately in `BUILD_STATUS.md`.

The live-mode **UI** is checked by `scripts/browser_live_ui.py`, which intercepts the API so the
client sees a live-capable server and a schema-accurate live run generated by the real engine:

```bash
mkdir -p .fixtures
docker compose run --rm --no-deps --user root \
    -v "$PWD/scripts/make_live_ui_fixture.py":/tmp/mk.py -v "$PWD/tests":/app/tests:ro \
    -v "$PWD/.fixtures":/out \
    backend python /tmp/mk.py --out /out/live-run.json
.venv-browser/bin/python scripts/browser_live_ui.py --base http://127.0.0.1:5183 \
    --fixture .fixtures/live-run.json
```

It verifies the mode selector, the connection label, the rendered draft with its proposed-content
markers, its grounded and unresolved evidence references, the rejected quotations and the model
record. It makes no Bedrock call. The runtime image does not contain `tests/`, so the fixture
generator needs it mounted as shown.

### Checking a real model, once credentials exist

`scripts/browser_live_run.py` drives one real live workflow through the Web UI. It intercepts
nothing, so **it makes billable model calls** and refuses to start without `--allow-paid-call`:

```bash
.venv-browser/bin/python scripts/browser_live_run.py --base http://127.0.0.1:5183 \
    --allow-paid-call --screenshots docs/screenshots/live
```

It performs exactly one run over the three fictional demo documents, then verifies discovery, the
semantic assessment, the drafted artifact, exact-quotation confirmation, provenance, the Markdown and
JSON exports and a saved-run reload. It prints the model ID, region, per-call and total token usage,
elapsed time, rejected quotations and each draft's grounded and unresolved evidence references for
`BUILD_STATUS.md`. Keep the bounds small (`LLM_MAX_CANDIDATES`, `LLM_MAX_MODEL_CALLS`) so one check
costs a known number of calls; do not re-run it to explore model wording. Do not describe a fixture
result as a live result.

Credential setup is the usual failure point rather than the model. Two traps, both documented in
`.env.example` and the `compose.llm.yml` header:

- The credential provider needs its own region. `BEDROCK_REGION` configures the Bedrock client only,
  so a profile with no `region` key raises `NoRegionError`, which the adapter can only report as an
  unrecognised model ID. Set `AWS_REGION` as well.
- A profile created by `aws login` cannot be used through `compose.llm.yml`: it refreshes its token
  by writing to `~/.aws/login/cache`, and that mount is read-only by design. Export short-lived
  credentials into the environment instead and leave `AWS_PROFILE` unset.

## CI

The included GitHub Actions workflow runs container tests and builds/starts the app, then exercises the HTTP/restart script. It does not require AWS credentials or paid inference. **It passed on `origin/main` for the initial commit `2cd0b6a`.** A passing run covers only the commit it ran against: uncommitted working-tree changes have not been through it, and a green CI run is not a substitute for the local acceptance steps above. UI automation remains a separate local acceptance step. `apps/web/package-lock.json` is committed, so the frontend image builds with `npm ci`.
