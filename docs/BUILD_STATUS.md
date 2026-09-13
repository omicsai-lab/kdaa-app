# Build status ledger

**Snapshot: 2026-09-13 (live-Bedrock-verified milestone). Version: 0.1.0.**

> This checkout is under version control: remote `origin`
> <https://github.com/omicsai-lab/kdaa-app.git>, with `HEAD` and `origin/main` both at `2cd0b6a`.
> **GitHub Actions CI passed for that initial commit.** The changes described in the current
> milestone below are **uncommitted** at the time of writing: they have passed the local checks
> recorded here and have **not** run in remote CI.
>
> Entries from earlier milestones are kept as a historical record of when they were written. Where
> such an entry describes the remote as absent or a CI run as uninspected, read it as the state at
> that time, not as the state of this checkout. Test results, versions and counts are unchanged and
> were produced by the runs described. Git operations are performed manually; see
> [DEVELOPMENT.md](DEVELOPMENT.md).

**Overall: one real Bedrock workflow has now been executed end to end from the Web UI. The earlier blocker is cleared.** Reference mode and all previously saved runs are unaffected. The live run used `us.amazon.nova-lite-v1:0`, not an Anthropic model: Anthropic models on this account are gated behind a Bedrock use-case form that was not submitted (see "Live Bedrock verification" below).

**Previous milestone (local integration) remains complete and passing.** Docker Compose build, real PostgreSQL integration tests, real browser acceptance over HTTP through nginx, and restart persistence all ran here with the results below. This is **not** an AWS deployment and not a validated assessment instrument.

## Laptop environment

| Item | Value |
| --- | --- |
| Host | macOS 15.7.9 (build 24G830), arm64 (Apple silicon) |
| Docker | Engine/client 29.4.3, Compose v5.1.3, context `desktop-linux` |
| Containers | `python:3.12-slim-bookworm` (Python 3.12.14), `postgres:17-bookworm` (PostgreSQL 17.11), `nginx:1.28-alpine` (nginx 1.28.3), `node:24-bookworm-slim` (Node 24.21.0, npm 11.19.0) |
| Host Python | 3.13.15 (used only for the stdlib-only `smoke_api.py` and the isolated `.venv-browser`) |
| Host Node | **Not installed.** The lockfile and web build were produced in a Node 24 container; no global Node/Python environment was changed. |
| Browser automation | Playwright 1.62.0, Chromium headless shell 151.0.7922.34, isolated `.venv-browser` |

Installed runtime versions inside the built backend image, matching `requirements.lock` exactly: fastapi 0.128.2, pydantic 2.13.4, SQLAlchemy 2.0.50, alembic 1.18.4, psycopg 3.3.3, starlette 0.50.0, uvicorn 0.48.0, **pypdf 6.18.1**, python-docx 1.2.0. The security-motivated pypdf pin was installed fresh and all PDF tests ran against it; 5.9.0 was not reintroduced anywhere.

The other KDAA prototype was untouched. Its images (`kdaa-backend`, `kdaa-migrate`, `postgres:16-alpine`) and two unrelated exited containers remain on this Docker host; no container, volume or port of theirs was started, stopped or removed. Ports 5183/8183 were free and the Compose project name `kdaa-app` was unused.

## Executed on this laptop

| Check | Result | Evidence / command |
| --- | --- | --- |
| npm lockfile generated online | **Passed** | Node 24 container, `npm install --package-lock-only --ignore-scripts --no-audit --no-fund`. Real registry resolution, lockfileVersion 3, 71 resolved packages. Committed as `apps/web/package-lock.json`. Not fabricated. |
| Clean frontend install | **Passed** | `npm ci --no-audit --no-fund` from the committed lock in a fresh container. |
| Frontend production build | **Passed** | `npm run build` = strict `tsc --noEmit` + `vite build` (Vite 7.3.1, React 19.2.0). 31 modules; `index.js` 219.16 kB (68.66 kB gzip), `index.css` 20.66 kB (5.44 kB gzip). A full type check, not transpilation. |
| `docker compose config` | **Passed** | Both `docker-compose.yml` and `compose.test.yml` validate. |
| `docker compose build` | **Passed** | Both images built: `kdaa-app-backend` (346 MB), `kdaa-app-frontend` (92 MB). |
| Container test suite, real PostgreSQL | **Passed — 64 passed, 0 skipped** | `docker compose -p kdaa-app-tests -f compose.test.yml up --build --abort-on-container-exit --exit-code-from tests`. All three PostgreSQL integration tests **executed**; none skipped. `KDAA_REQUIRE_POSTGRES_TESTS=1` enforced. Disposable `kdaa_app_test` database, random `kdaa_test_<hex>` schema dropped afterwards; no application volume used. |
| Fresh Alembic migration vs. metadata | **Passed** | `test_fresh_migration_matches_current_metadata` — `compare_metadata` returns no drift on a fresh schema. |
| Interrupted-run recovery on PostgreSQL | **Passed** | `test_postgres_run_recovery`. |
| Application stack start and health | **Passed** | `docker compose up --build -d`; postgres, backend and frontend all report `healthy`. Startup, migration and uvicorn logs clean; no ERROR/FATAL in postgres or nginx logs. |
| HTTP endpoints | **Passed** | `GET /ready` → `{"ready":true}`; `/docs` 200; `/openapi.json` 200 (18 routes); web root 200 serving the built client; `/api/v1/workspaces` 200 **through the nginx same-origin proxy** on 5183. |
| Real HTTP + backend restart persistence | **Passed** | `python3 scripts/smoke_api.py --base http://127.0.0.1:8183 --restart` → `{"passed": true, "documents": 3, "candidates": 5, "restart_persistence_checked": true}`. |
| Browser acceptance (demo path) | **Passed** | `.venv-browser/bin/python scripts/browser_smoke.py --base http://127.0.0.1:5183`. Real Chromium over HTTP through nginx: workspace creation, three demo imports, **5 input-derived candidates**, exact evidence highlighting with "Exact quote verified", assessment limitations, amplification verification gate, JSON export download (3 sources), page refresh, provenance `run · succeeded`, desktop 1440px and 390px layout with no horizontal overflow. No page errors. |
| Browser acceptance (upload matrix) | **Passed** | `.venv-browser/bin/python scripts/browser_uploads.py --fixtures .fixtures` — see the table below. |
| Stale-asynchronous-state regression | **Passed** | `.venv-browser/bin/python scripts/browser_stale_state.py` — both scenarios green; both confirmed failing against the pre-fix client. See "Client review fixes" below. |
| Full-stack persistence across `down` + `up -d` | **Passed** | Normal `docker compose down` (never `-v`) then `up -d`. A read-only API snapshot of all 5 workspaces, 10 documents and 5 runs — including run status, `config_sha256`, candidate/evidence counts, evidence text hashes, provenance event counts and terminal event — was **byte-identical** before and after. Source bytes re-downloaded after restart still match their stored `raw_sha256`, and normalized text matches `text_sha256`. |
| Localhost-only access preserved | **Passed** | Published ports bind `127.0.0.1:5183` and `127.0.0.1:8183` only. From this host's LAN address `192.168.1.163`, both ports refuse the connection. PostgreSQL publishes no host port. No binding, origin/host check or parser bound was relaxed. |

### Upload matrix and edge cases (real browser, real files)

| Case | Result |
| --- | --- |
| Real text-based PDF (`upload-software.pdf`, pypdf 6.18.1) | **Passed** — imported, extracted, cited in results |
| Real DOCX with body paragraphs and a table | **Passed** — imported and extracted |
| Run over the uploaded PDF + DOCX | **Passed** — 3 candidates derived from the uploaded text, evidence inspector shows "Exact quote verified" |
| Markdown export | **Passed** — 6,775-byte Markdown download citing `upload-software.pdf` |
| JSON export | **Passed** — 2 sources in the exported bundle |
| Duplicate source adds no independent support | **Passed** — UI shows the "Duplicate text" badge; the run reports "2 document(s), 1 unique text(s). 1 duplicate(s) not counted again" and still yields 1 candidate |
| Irrelevant TXT | **Passed** — 0 candidates, with the "No supported candidates under these rules" message that explicitly does not claim the source is worthless |
| Malformed PDF and malformed DOCX | **Passed** — readable error "The PDF or DOCX is malformed or unsupported." with no traceback, no partial import, and `/ready` still true afterwards (service did not crash) |
| Reload and provenance after uploads | **Passed** — sources and `run · succeeded` still present |

## Failure found and fixed

**Run configuration was not round-trip stable through PostgreSQL.** `test_real_persistence_survives_new_connection` failed on the first real-PostgreSQL execution: a run reloaded from PostgreSQL did not equal the run just executed.

- Cause: `Run.config` embedded `asdict(rule)` values directly, so `rules[].kind` was a `CandidateType` (a `StrEnum`) and `rules[].terms` was a `tuple`. Persisting serializes the config to JSON, which returns `str` and `list`. The in-memory object and its persisted form were therefore never equal. The test-only `MemoryStore` deep-copies Python objects, so the 61 non-PostgreSQL tests could not detect this — exactly the gap real PostgreSQL execution was meant to close.
- Fix: `src/kdaa/core/service.py` now stores the canonical JSON that `config_sha256` is computed over (`config = json.loads(canonical)`), so the persisted config is identical to the in-memory one. One added line plus a comment; no schema, migration, contract or rule change.
- `config_sha256` is unchanged: `json.dumps` already coerced the same values when producing the hash, so existing hashes remain valid.
- Verified: full container suite rerun → 64 passed, 0 skipped.

No test, assertion, bound or skip was weakened to obtain a green result.

## Client review fixes (second pass)

Three narrow review items were applied after the first integration commit. No architecture, boundary,
dependency pin, provenance contract or scope change.

### 1. Stale asynchronous responses in the Web client

`apps/web/src/App.tsx` applied every asynchronous reply unconditionally. A reply issued for one workspace
or run could land after the user had moved on and overwrite the current view.

- The run poll was the clearest case: `api(...).then(setRun)` had no guard at all, so a poll for workspace
  A's run would install that run — candidates, evidence and all — into workspace B. Reproduced in a real
  browser: workspace B rendered workspace A's five demo candidates.
- `chooseRun`, `startRun`, `inspectEvidence`, `viewDocument`, `download`, `upload`, `loadDemo` and
  `refreshDocuments` had the same exposure for run, evidence, content, provenance and error state.
- Fix: each handler marks the selection it was issued for and discards its result if that selection moved
  on. Two counters, not one — switching workspace advances both, selecting another run advances only the
  run counter, so a pending document read survives a run change but never a workspace change. Errors are
  guarded on the same mark, so an obsolete request cannot raise an alert on the current view.
- Modal evidence and content are also cleared on workspace switch, which was the same stale-state family.

### 2. Terminal-status refresh

When polling observed a terminal status, the old client updated only the run object. The run-history
summary still showed `running` and the provenance trail was never re-read. The poll now refreshes that
run's history summary and provenance on completion, and deliberately leaves the user's workspace, run and
tab selection untouched. The refresh is guarded on the selection mark rather than the effect's lifetime,
because the status change tears the polling effect down in the same tick.

### 3. Regression coverage

`scripts/browser_stale_state.py` drives a real browser and intercepts API responses to control timing that
the synchronous backend cannot express — holding a poll open, and reporting a completed run as still
running. It asserts both behaviours above.

**Both scenarios were confirmed to fail against the pre-fix client before the fix was restored**, so this
is genuine regression coverage rather than a test written to match current behaviour:

| Scenario | Pre-fix result | Post-fix result |
| --- | --- | --- |
| Poll in flight during a workspace switch | **Failed** — workspace B displayed workspace A's "Candidate knowledge assets" and its five demo candidates | **Passed** — stale reply discarded; workspace B stays empty, no error alert |
| Poll reaches a terminal status | **Failed** — provenance stayed at its stale length, history summary still read `running` | **Passed** — history summary became `succeeded`, provenance went from 1 to 11 events, selected run and Provenance tab unchanged |

A JavaScript unit-test runner was deliberately **not** added: the project boundaries in
[DEVELOPMENT.md](DEVELOPMENT.md) rule out introducing new frameworks during local integration, and
Playwright — already the browser-acceptance tool here — expresses the delayed-response scenarios
directly against the real built client and the real API.

### 4. Source manifest

`SOURCE_SHA256SUMS.txt` was regenerated over the current tracked source, excluding build output
(untracked `dist/`, `node_modules/`) and the manifest itself. It covered 85 files at that point and
deliberately omitted the generated `apps/web/package-lock.json`; the live-inference milestone now
includes the lockfile, so the current manifest covers 98 files. Verified with
`shasum -a 256 -c SOURCE_SHA256SUMS.txt` — all files OK.

## Live-inference milestone (2026-09-13)

### What was implemented

Two run modes, selected per run, with no automatic movement between them. `reference` is unchanged and
remains the default. `live` uses **one configurable Bedrock model through the official Converse API**,
verified against the current boto3 `bedrock-runtime.converse` reference (`modelId`, `messages`,
`system`, `inferenceConfig.maxTokens`; response `output.message.content[].text`, `stopReason`,
`usage`). No model is guessed and no credential is hard-coded: `BEDROCK_REGION` and
`BEDROCK_MODEL_ID` must both be supplied, credentials come only from the standard boto3 chain, and
the client is never constructed unless `KDAA_LLM_ENABLED=1`.

Three bounded stages: discovery (specific hypotheses with verbatim quotations, **not** filtered by
the reference keyword rules), assessment (relevance to the goal, possible uses, limitations, missing
evidence — no score, no ranking), and amplification (an actual drafted artifact per retained
candidate, not instructions to produce one).

`src/kdaa/providers/bedrock.py` is the only module importing the AWS SDK; the `LLMProvider` contract
is plain text in, text out, so the provider interface stays vendor-neutral.

### Evidence integrity

The model is never asked for, and never believed about, a character offset. It supplies a document id
and a quotation; `src/kdaa/core/quotes.py` finds that quotation in the stored normalized text.
Zero occurrences is rejected as unfounded, more than one as ambiguous. Each rejection is recorded as
an `EvidenceRejection` with a reason and as an `evidence.rejected` provenance event, and a candidate
whose quotations all fail is dropped rather than repaired. Document text is passed inside delimited
`<document>` blocks with a system rule stating that instructions inside it are content, not commands.

### Schema versioning and preserved runs

Live bundles carry `schema_version` 2.0; reference bundles stay 1.0. Every 2.0 field is optional with
a default, so runs stored before live mode existed parse unchanged. Semantic assessment is a separate
`SemanticAssessment` record: the lexical fields stay `not_assessed`/empty in live mode rather than
being repurposed.

### Executed on this laptop

| Check | Result | Evidence |
| --- | --- | --- |
| Container suite with real PostgreSQL | **Passed — 124 passed, 0 skipped** | Up from 64. `docker compose -p kdaa-app-tests -f compose.test.yml up --build --abort-on-container-exit --exit-code-from tests`. All PostgreSQL integration tests execute. |
| Live pipeline with provider fixtures | **Passed — 31 tests** | `tests/test_llm_engine.py`. Covers unfounded / ambiguous / empty / oversized / cross-document quotations, malformed and non-JSON responses, an incomplete assessment, provider timeouts and denied access, the input-size and model-call budgets, and that a candidate with no resolvable quotation is dropped rather than repaired. |
| Bedrock adapter with a fake client | **Passed — 15 tests** | `tests/test_bedrock_adapter.py`. Request shaping in the documented Converse form; `max_tokens` refused as truncation; filtered and empty output refused; AccessDenied / ResourceNotFound / Validation / Throttling / ModelTimeout / ServiceUnavailable translated; SDK messages never echoed; `total_max_attempts` (which counts the initial request) asserted. |
| Previously saved runs preserved | **Passed** | `tests/test_legacy_runs.py` plus a live check against the running stack: **39 of 39 stored runs across 31 workspaces** load, export and still report `provider: null`, `schema_version: 1.0`, `discovery_basis: lexical_rules`, no semantic block and no draft. The oldest run's Markdown export still reads "No live LLM calls". |
| Frontend typecheck and production build | **Passed** | Strict `tsc --noEmit` + Vite 7.3.1. |
| Compose build and healthy stack | **Passed** | Both compose files validate, including the `compose.llm.yml` credential overlay. All three services healthy; the 24 pre-existing workspaces (31 after this session's checks) survived every rebuild. |
| `smoke_api.py --restart` | **Passed** | 3 documents, 5 candidates, restart persistence confirmed. |
| `browser_smoke.py` | **Passed** | Reference demo path unchanged. |
| `browser_uploads.py` | **Passed** | PDF/DOCX/duplicate/irrelevant/malformed/exports unchanged. |
| `browser_stale_state.py` | **Passed** | Previous milestone's regressions still fixed. |
| Live-mode UI rendering | **Passed** | `scripts/browser_live_ui.py`, against a schema-accurate live bundle produced by the real engine. Verifies the Reference/Live AI selector, the connection label changing from "No cloud connection" to "Bedrock · <model>", the rendered draft with its proposed-content markers, both rejected quotations, and the model record. **It intercepts the API and makes no Bedrock call.** |
| Credential handling reaches the real chain | **Passed (as a negative result)** | With live mode enabled inside the running container, the provider reaches botocore's credential chain and returns `llm_no_credentials` (503) with an actionable message. This confirms wiring, and confirms that no credentials exist here. |
| No secrets or content in logs | **Passed** | Backend logs contain no prompt, document text, model output or AWS variable. Failure logging records only run id, mode, stage and exception class. |

### Bug found and fixed during verification

Compose forwards unset `AWS_*` variables as empty strings, and botocore reads an empty `AWS_PROFILE`
as a request for a profile literally named `""`, failing with `ProfileNotFound` before the credential
chain runs. An operator enabling live mode without setting `AWS_PROFILE` would have hit a confusing
crash instead of a credentials message. `kdaa_api/engines.py` now drops empty `AWS_*` values before
the client is built, covered by a regression test. Verified: the same configuration now returns
`llm_no_credentials` with an actionable message.

### Blocked

| Check | Status | Why |
| --- | --- | --- |
| **Real Bedrock inference** | **Cleared 2026-09-13** | Was blocked: no AWS credentials, CLI or `~/.aws` existed at the time. The AWS CLI and a `kdaa` login profile have since been installed, and one real workflow has now run. See "Live Bedrock verification" above. |
| Live token usage | **Cleared 2026-09-13** | Recorded from the Converse response: 3,099 input / 1,937 output / 5,036 total tokens over 4 calls. Cost figures are still not produced; Bedrock returns usage, not price. |
| GitHub Actions CI | **Not run at the time** | No remote CI run was inspected during that milestone. Since then, CI has passed for the initial commit `2cd0b6a` on `origin/main`. |
| AWS infrastructure (ECR/ECS/RDS/S3/Cognito/Terraform) | **Out of scope** | Explicitly excluded from this milestone. |
| Multi-agent frameworks, vector databases, iOS | **Out of scope** | Not added. |

### Honest limits

Fixture tests prove the Core's handling of model output and the adapter's request shaping and failure
translation. **They prove nothing about a real Bedrock endpoint**, about model quality, about how
often a real model produces unfounded quotations, or about latency and cost. The live-mode UI check
renders a fixture bundle and makes no model call. No live integration claim is made anywhere in this
repository.

## Live Bedrock verification (2026-09-13)

**One real live workflow was executed from the Web UI.** This is the first entry in this ledger
describing a real model call; everything above it that says "fixtures only" still says so correctly.

### The run

| Item | Value |
| --- | --- |
| Model ID | `us.amazon.nova-lite-v1:0` (US inference profile) |
| Region | `us-east-1` |
| Provider | `aws-bedrock-converse` · engine `live-converse-v0.1` · bundle `schema_version` 2.0 |
| Account / identity | `371510064316`, `arn:aws:iam::371510064316:user/james`, via the existing `kdaa` profile |
| Input | The three fictional demo Markdown documents, 1,728 characters total |
| Model calls | **4** (1 discovery + 1 assessment + 2 amplification), budget 5, `LLM_MAX_CANDIDATES=3` |
| Token usage | **input 3,099 · output 1,937 · total 5,036** |
| Elapsed | **12.6 s** wall clock; 12,598 ms summed provider latency |
| Per call | discovery 961/390 (2,153 ms) · assessment 800/376 (2,680 ms) · amplification 673/619 (3,768 ms) · amplification 665/552 (3,997 ms) — input/output tokens, `stopReason` `end_turn` throughout |
| Run / workspace | run `60ee40c3-e80e-4d7b-9865-af751824138d` in workspace `Live Bedrock acceptance d27f29` |
| Rejected quotations | **0** |
| Cost | Not itemised. Bedrock did not return a price and none was inferred. |

### What was verified

| Check | Result | Evidence |
| --- | --- | --- |
| Discovery from a real model | **Passed** | 2 candidates, both `discovery_basis: model_reading`, both `status: provisional` / `ownership: unresolved`: "Copperfin text-cleaning utility" (software) and "Meadow observations dataset" (dataset). |
| Exact quotations | **Passed** | 4 evidence items, each resolved by `quotes.resolve` against the stored normalized text and re-validated by `validate_evidence`; the inspector shows "Exact quote verified". Offsets came from the backend, never from the model. |
| Semantic assessment | **Passed** | Every candidate has a `SemanticAssessment` with explanation, possible uses and limitations. Lexical fields stayed `not_assessed` with no overlapping terms — not repurposed, and no score produced. |
| Drafted artifacts | **Passed** | 2 real Markdown drafts (1,272 and 1,408 characters): "Teaching Exercise: Cleaning Field Notes with Copperfin" and "Meadow Observations Dataset Analysis Exercise" — actual exercises, not instructions for writing one. 7 and 6 proposed elements declared. |
| Draft grounding | **Passed** | Each draft cited 2 valid evidence ids; `unresolved_evidence_refs` empty for both. The fix below was exercised on real model output, not only on fixtures. |
| Provenance | **Passed** | 19 events: 4 × `llm.call` (one per model call, each with prompt version, hashes, record key, stop reason, usage and latency), 0 × `evidence.rejected`, 0 × `draft.grounding_unresolved`. |
| Exports | **Passed** | Markdown 12,190 bytes citing the demo sources and listing each draft's source-supported evidence ids; JSON bundle with 3 sources and `simulated: false`. |
| Saved-run reload | **Passed** | After reload the run still renders as Live AI mode with its model record intact: "Produced by aws-bedrock-converse · model us.amazon.nova-lite-v1:0 · region us-east-1 · 4 model call(s) · tokens inputTokens=3099, totalTokens=5036, outputTokens=1937". The mode selector had reset to Reference, and the saved run still read as live — a saved run keeps its own labelling. |
| Prompts and responses off the database | **Passed** | Recorded under `llm/<run-id>/` in the source-asset volume; the bundle keeps only hashes and keys. |
| Reference mode unaffected | **Passed** | `browser_smoke.py`, `browser_uploads.py`, `browser_stale_state.py` and `smoke_api.py --restart` all still pass, and the 38 pre-existing workspaces survived every rebuild. |

Commands:

```bash
eval "$(aws configure export-credentials --profile kdaa --region us-east-1 --format env)"
docker compose up -d --build
.venv-browser/bin/python scripts/browser_live_run.py --base http://127.0.0.1:5183 --allow-paid-call
```

### Credential setup: three real failures found and fixed

The model was never the problem. Each of these presented as a misleading error.

1. **The `:ro` mount in `compose.llm.yml` cannot serve an `aws login` profile.** That flow refreshes
   its token by *writing* to `~/.aws/login/cache`; in-container it failed with
   `OSError: Read-only file system: /home/kdaa/.aws/login/cache/tmp_*`. The mount stays read-only by
   design — credentials are not writable by the container. Fixed by documenting the environment
   credential path for such profiles in `compose.llm.yml`, `.env.example`, `README.md` and
   `docs/TESTING.md`, and using it here. No secret is written to disk: the credentials are exported
   into the shell for the single `docker compose up` that consumes them.
2. **The credential provider needs its own region.** `BEDROCK_REGION` configures the Bedrock client
   only. Profile `kdaa` has no `region` key, so the login provider raised `NoRegionError`, which the
   adapter could only surface as "Bedrock does not recognize this model or inference profile ID" —
   pointing at the model rather than at the credentials. Fixed by documenting `AWS_REGION` alongside
   `BEDROCK_REGION` in the same four places.
3. **Anthropic models are gated on this account.** `us.anthropic.claude-haiku-4-5-20251001-v1:0`
   returned `ResourceNotFoundException: Model use case details have not been submitted for this
   account. Fill out the Anthropic use case details form before using the model.` A probe of that
   exact model from the host CLI had succeeded earlier in the session, so the gate is account state,
   not a wrong ID. `us.amazon.nova-lite-v1:0`, `global.amazon.nova-2-lite-v1:0` and
   `us.amazon.nova-micro-v1:0` were all verified callable with a single 5-token probe each; Nova Lite
   was used for the run. **To use an Anthropic model, submit that form in the Bedrock console, then
   set `BEDROCK_MODEL_ID` and restart** — no code change is needed.

Two runs failed before these were understood (`0c4000cb…` and `d0413a10…`, both in their own
`Live Bedrock acceptance …` workspaces). They are left in the local database as an honest record;
each spent one model call and produced no partial result.

### Amplification grounding: bug found and fixed

`LiveEngine._amplify` ended with `grounded_evidence_ids=grounded or list(candidate.evidence_ids)`.
When the model supplied no usable grounding reference — key absent, not a list, or every id invalid —
the draft silently inherited **all** of the candidate's verified evidence. That presented every
excerpt as support the model had never claimed, and invalid references were dropped without trace.
The `ArtifactDraft` docstring already said "only `grounded_evidence_ids` is backed by verified source
quotations", so the fallback contradicted the record's own contract.

- Valid ids are preserved in order and de-duplicated; there is no fallback.
- A reference that does not parse as a UUID, or that is not one of this candidate's verified evidence
  ids, is recorded verbatim in the new optional `ArtifactDraft.unresolved_evidence_refs` (schema 2.0,
  default empty, so stored runs parse unchanged).
- A draft whose references were missing or unresolvable emits a `draft.grounding_unresolved`
  provenance event carrying the grounded count, the unresolved references and
  `cites_no_verified_evidence`, so "this draft cites nothing" is auditable rather than inferred.
- The Markdown export states plainly when a draft cited no verified evidence and lists unresolved
  references; the Web client shows both, replacing the old unconditional "Grounded in N excerpt(s)".

**Regression coverage: 6 new tests in `tests/test_llm_engine.py`, 5 of which were confirmed failing
against the pre-fix engine** before the fix was restored — valid-reference preservation, missing key,
invalid and unparseable references, de-duplication, a non-list value, and the export wording. One
existing assertion (`draft.grounded_evidence_ids == candidate.evidence_ids`) had encoded the buggy
behaviour and was corrected. `scripts/make_live_ui_fixture.py` now emits one valid and one
unresolvable reference, and `browser_live_ui.py` asserts both render.

### Executed on this laptop

| Check | Result | Evidence |
| --- | --- | --- |
| Container suite with real PostgreSQL | **Passed — 130 passed, 0 skipped** | Up from 124. `docker compose -p kdaa-app-tests -f compose.test.yml up --build --abort-on-container-exit --exit-code-from tests`. |
| Pre-fix failure confirmed | **Passed** | The old fallback was restored temporarily: 5 failed, 125 passed. Fix restored, suite green again. |
| Frontend typecheck and production build | **Passed** | `npm ci && npm run build` in a `node:24-bookworm-slim` container — strict `tsc --noEmit` + Vite 7.3.1. 31 modules; `index.js` 224.72 kB (70.24 kB gzip), `index.css` 22.08 kB (5.75 kB gzip). |
| Live-mode UI rendering (fixtures) | **Passed** | `scripts/browser_live_ui.py`, now also asserting the grounded line and the unresolved-reference block. Makes no Bedrock call. |
| Reference demo path | **Passed** | `scripts/browser_smoke.py` — 5 candidates, exports, refresh, 390px layout. |
| Upload matrix | **Passed** | `scripts/browser_uploads.py` — PDF/DOCX/duplicate/irrelevant/malformed/exports unchanged. |
| Stale-asynchronous-state regressions | **Passed** | `scripts/browser_stale_state.py` — both scenarios still green. |
| HTTP + restart persistence | **Passed** | `smoke_api.py --restart` → 3 documents, 5 candidates, restart persistence confirmed. |
| Source manifest | **Passed** | `SOURCE_SHA256SUMS.txt` regenerated over 101 files (96 + `scripts/browser_live_run.py` + 4 live-run screenshots); `shasum -a 256 -c` reports 101 OK, 0 failures. |
| `docs/openapi.json` | **Regenerated** | 15 paths / 18 operations, unchanged in count; `ArtifactDraft` now documents `unresolved_evidence_refs`. |

### Still not run

| Check | Status | Why |
| --- | --- | --- |
| Live run against an Anthropic model | **Blocked** | The Bedrock "Anthropic use case details" form has not been submitted for account `371510064316`. Only the account owner can do that; no code change is required afterwards. |
| Live cost figures | **Not run** | Bedrock returns token usage, not price. Token counts are recorded above; no cost was inferred. |
| GitHub Actions CI for these changes | **Not run** | CI passed for the initial commit `2cd0b6a` on `origin/main`. The changes in this milestone are still uncommitted, so remote CI has not run against them. The local checks above are what has been verified. |
| AWS infrastructure (ECR/ECS/RDS/S3/Cognito/Terraform) | **Out of scope** | Not started. |

### Honest limits

One successful run against one small fictional input proves that the wiring works end to end: real
credentials, a real Converse call, real quotation resolution, real persistence and real export. It
proves **nothing** about model quality, about how often a real model produces unfounded quotations
(this run produced none, which is one observation, not a rate), about latency or cost at any other
size, or about any other model. Nova Lite's output is not evidence about Anthropic models. The
engine's guarantees — that a quotation is resolved against stored text or rejected, and that a draft
never inherits grounding it did not claim — hold regardless of model, and are what the test suite
covers.

## Reproducing the live run

**This step is done** — it is kept because it is how the live run is repeated, not because anything
is outstanding. The working configuration on this laptop is the `kdaa` login profile plus exported
environment credentials; see "Credential setup" above for the three traps that are easy to hit.

```bash
cd "$(git rev-parse --show-toplevel)"

# The `kdaa` profile is an `aws login` session. Refresh it when it expires (it expires often):
aws login --profile kdaa --region us-east-1

# Export short-lived credentials into the shell and start the stack in the same command, so no
# secret is written to disk. Leave AWS_PROFILE unset in .env when doing this.
eval "$(aws configure export-credentials --profile kdaa --region us-east-1 --format env)"
docker compose up -d --build
curl -s http://localhost:8183/api/v1/system | grep -o '"live_llm":[a-z]*'

# One live workflow. Refuses to start without the flag; keep the bounds small.
.venv-browser/bin/python scripts/browser_live_run.py --base http://127.0.0.1:5183 --allow-paid-call
```

The original setup instructions follow, for an account set up from scratch. Once you have an AWS
identity with `bedrock:InvokeModel` and model access enabled in your chosen region:

```bash
cd "$(git rev-parse --show-toplevel)"

# 1. Choose a Converse-capable model or inference profile that your account can actually use.
aws bedrock list-inference-profiles --region us-east-1 --query 'inferenceProfileSummaries[].inferenceProfileId'

# 2. Put the choice, and your profile name, in an untracked .env (never in Git).
cat >> .env <<'ENV'
KDAA_LLM_ENABLED=1
BEDROCK_REGION=us-east-1
BEDROCK_MODEL_ID=<the id you picked above>
AWS_PROFILE=<your profile name>
ENV

# 3. Restart with the read-only credential overlay, then confirm the server offers live mode.
docker compose -f docker-compose.yml -f compose.llm.yml up -d --build
curl -s http://localhost:8183/api/v1/system | grep -o '"live_llm":[a-z]*'
```

Then open http://localhost:5183, create a workspace, click **Load demo**, choose **Live AI**, and run
it. Report the model ID, region, token usage, candidate count and any rejected quotations, and this
ledger will be updated with the real result.

If you would rather not use a profile, put short-lived `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
and `AWS_SESSION_TOKEN` in `.env` instead and use the ordinary `docker compose up -d --build`.

## Changes made during local integration

| File | Reason |
| --- | --- |
| `apps/web/package-lock.json` | **New.** First real online dependency resolution (71 packages). |
| `apps/web/Dockerfile` | Removed the pre-lockfile `npm install` fallback; the build is now unconditionally `npm ci`, which is the documented step once a real lock exists and the clean build passes. |
| `src/kdaa/core/service.py` | The run-config persistence fix described above. |
| `scripts/browser_uploads.py` | **New.** Automates the required upload matrix and edge cases that `browser_smoke.py` does not cover (PDF, DOCX, Markdown export, duplicate, irrelevant, malformed). |
| `scripts/make_upload_fixtures.py` | **New.** Generates those fixtures with the pinned parser libraries so the check is reproducible. |
| `.github/workflows/ci.yml` | The missing-lockfile warning became a hard error, since the image no longer has a fallback. |
| `.gitignore` | Ignore the disposable `.fixtures/` directory. |
| `apps/web/src/App.tsx` | Discard asynchronous replies from a superseded workspace/run; refresh history and provenance when a poll reaches a terminal status. |
| `scripts/browser_stale_state.py` | **New.** Regression coverage for both behaviours, using held and rewritten API responses. |
| `SOURCE_SHA256SUMS.txt` | Regenerated over current source, excluding generated dependencies, build output and itself. |
| `docs/TESTING.md`, `docs/BUILD_STATUS.md` | Real laptop results and the new commands. |

No dependency pin was changed. No architecture, Core/API/frontend boundary, provenance contract or localhost binding was altered.

## Not run / out of scope

| Check | Status | Note |
| --- | --- | --- |
| GitHub Actions CI | **Not run at the time** | Workflow source was updated, but no remote run was inspected during that milestone. CI has since passed for the initial commit `2cd0b6a`. |
| Optional host-Python venv suite | **Not run** | Superseded by the container suite, which is the acceptance gate and covers real PostgreSQL. |
| Image-only/scanned PDF, OCR, encrypted PDF | **Out of scope** | Explicitly unsupported; rejection is covered by the unit suite. |
| AWS / Bedrock / S3 / Cognito / Terraform / MCP / agents / queues / iOS | **Out of scope** | Not started. Propose separately. |

## Known caveats

- The engine remains `reference-rules-v0.1`: deterministic literal English term rules, not an LLM and not Paper B. Candidates stay provisional with unresolved ownership.
- Single-user, unauthenticated localhost PoC. Do not expose it to a LAN, tunnel or shared host. The local database password is deliberately nonsecret.
- The provenance ledger is application-controlled traceability, not tamper-proof storage.
- There is no delete/reset UI. `docker compose down -v` would destroy the database and source volumes; it was never run and should not be.
- The acceptance runs left several clearly named workspaces (`Acceptance …`, `Browser acceptance …`, `Upload acceptance …`) in the local database for inspection. They are harmless; there is no UI to remove them in this version.
- Docker Desktop must be running before any Compose command.

## How to run it

```bash
docker compose up --build -d          # http://localhost:5183
docker compose ps                     # wait for all three services to be healthy
docker compose down                   # stop; data survives. Never add -v.
```

## Immediate next milestone

Local integration is complete and one real Bedrock workflow has been verified; stop at this boundary. Nothing here is deployed to AWS, and no infrastructure work was started. The next step is a separate, explicitly approved decision: either publish this work through the manual Git steps in [DEVELOPMENT.md](DEVELOPMENT.md), or scope the AWS path (ECR/ECS Fargate + RDS/S3/Bedrock) as its own piece of work. Submitting the Bedrock Anthropic use-case form would allow the same run against an Anthropic model with no code change.

## Resume log

```text
Date / environment: 2026-09-12, macOS 15.7.9 arm64, Docker 29.4.3 / Compose 5.1.3, branch local-integration
Changed files and reason:
  apps/web/package-lock.json  - first real online npm resolution (71 packages)
  apps/web/Dockerfile         - npm install fallback removed; npm ci only
  src/kdaa/core/service.py    - store canonical JSON config so a PostgreSQL round trip is lossless
  scripts/browser_uploads.py, scripts/make_upload_fixtures.py - automate the required upload matrix
  .github/workflows/ci.yml, .gitignore, docs/TESTING.md, docs/BUILD_STATUS.md - checks and ledger
Commands actually executed:
  docker run --rm -v <web>:/web -w /web node:24-bookworm-slim npm install --package-lock-only --ignore-scripts
  docker run --rm -v <web>:/web -w /web node:24-bookworm-slim sh -c 'npm ci && npm run build'
  docker compose config
  docker compose build
  docker compose -p kdaa-app-tests -f compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
  docker compose -p kdaa-app-tests -f compose.test.yml down
  docker compose up --build -d ; docker compose ps ; docker compose logs backend frontend postgres
  python3 scripts/smoke_api.py --base http://127.0.0.1:8183 --restart
  .venv-browser/bin/python scripts/browser_smoke.py --base http://127.0.0.1:5183
  .venv-browser/bin/python scripts/browser_uploads.py --base http://127.0.0.1:5183 --fixtures .fixtures
  docker compose down ; docker compose up -d   (no -v; state compared before/after)
Passed: npm lock generation, npm ci, Vite production build + strict typecheck, compose config,
  compose build, 64/64 container tests with real PostgreSQL (0 skipped), stack health, HTTP endpoints,
  nginx proxy, smoke_api --restart, both browser acceptance scripts, down/up persistence with SHA-256
  integrity, localhost-only binding.
Failed: test_real_persistence_survives_new_connection on first run — fixed in src/kdaa/core/service.py,
  suite rerun green. No other failures.
Blocked / not run: GitHub Actions CI (no remote run inspected); optional host-Python venv suite (container
  suite used instead); AWS and all cloud work (deliberately out of scope).
Next concrete action: maintainer decision — publish this work manually, or scope AWS separately. No cloud work started.
```

```text
Date / environment: 2026-09-12 (second pass), macOS 15.7.9 arm64, Docker 29.4.3 / Compose 5.1.3, branch local-integration
Changed files and reason:
  apps/web/src/App.tsx          - discard asynchronous replies from a superseded workspace/run; on a
                                  terminal poll, refresh that run's history summary and provenance
                                  without changing the user's selection
  scripts/browser_stale_state.py - new regression coverage using held/rewritten API responses
  SOURCE_SHA256SUMS.txt         - regenerated over current tracked source (85 files)
  docs/TESTING.md, docs/BUILD_STATUS.md - commands and results for the above
Commands actually executed:
  docker run --rm -v <web>:/web -w /web node:24-bookworm-slim sh -c 'npm ci && npm run build'
  docker compose up --build -d
  docker compose -p kdaa-app-tests -f compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
  docker compose -p kdaa-app-tests -f compose.test.yml down
  python3 scripts/smoke_api.py --base http://127.0.0.1:8183 --restart
  .venv-browser/bin/python scripts/browser_stale_state.py --base http://127.0.0.1:5183
  .venv-browser/bin/python scripts/browser_smoke.py --base http://127.0.0.1:5183
  .venv-browser/bin/python scripts/browser_uploads.py --base http://127.0.0.1:5183 --fixtures .fixtures
  shasum -a 256 -c SOURCE_SHA256SUMS.txt
  git checkout HEAD -- apps/web/src/App.tsx  (temporary, to prove the new checks fail pre-fix; then restored)
Passed: strict typecheck + Vite build, 64/64 container tests with real PostgreSQL (0 skipped),
  smoke_api --restart, browser_stale_state (both scenarios), browser_smoke, browser_uploads,
  manifest verification.
Failed: nothing on the fixed client. Both new scenarios were verified failing against the pre-fix
  client on purpose, then the fix was restored and the stack rebuilt.
Blocked / not run: GitHub Actions CI (no remote run inspected); AWS and all cloud work (out of scope).
Next concrete action: maintainer reviews and publishes manually, then a separate decision on AWS scope.
```

```text
Date / environment: 2026-09-13, macOS 15.7.9 arm64, Docker 29.4.3 / Compose 5.1.3, branch llm-integration
Changed files and reason:
  src/kdaa/domain.py                  - schema 2.0 records (SemanticAssessment, ArtifactDraft,
                                        EvidenceRejection, LLMInteraction, ProviderRecord), all optional
  src/kdaa/providers/llm.py           - vendor-neutral text-in/text-out provider contract
  src/kdaa/providers/bedrock.py       - new: Bedrock Converse adapter, the only AWS SDK importer
  src/kdaa/core/prompts.py            - new: versioned prompts; document text delimited as data
  src/kdaa/core/quotes.py             - new: backend-side quotation resolution, model offsets ignored
  src/kdaa/core/llm_engine.py         - new: three bounded stages, budgets, rejections, no fallback
  src/kdaa/core/service.py            - engine registry, run modes, live provenance, recorded bodies
  src/kdaa/core/reports.py            - drafts, model record and rejections in the Markdown export
  src/kdaa/providers/local.py         - generated llm/<run>/<nn>-<stage>.json keys for recorded bodies
  services/api/kdaa_api/{config,engines,main}.py - opt-in live config, mode on StartRun, system info
  apps/web/src/{App.tsx,types.ts,styles.css}     - Reference/Live AI selector, mode-aware labels,
                                        rendered drafts, rejected quotations, model record
  tests/test_llm_engine.py, test_bedrock_adapter.py, test_legacy_runs.py, fixtures_legacy_run.json - new
  scripts/browser_live_ui.py, make_live_ui_fixture.py - live-mode UI rendering check
  docker-compose.yml, compose.llm.yml, .env.example, requirements.lock, pyproject.toml - config/deps
  README.md, docs/{ARCHITECTURE,TESTING,BUILD_STATUS,openapi.json} - documentation
Commands actually executed:
  docker compose -p kdaa-app-tests -f compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
  docker compose up --build -d ; docker compose -f docker-compose.yml -f compose.llm.yml config
  npm ci && npm run build   (in a node:24 container)
  python3 scripts/smoke_api.py --base http://127.0.0.1:8183 --restart
  .venv-browser/bin/python scripts/browser_{smoke,uploads,stale_state,live_ui}.py
  docker compose exec backend python -c "...build_engines(Settings()); provider.generate(...)"
Passed: 124/124 container tests with real PostgreSQL (0 skipped); live pipeline and Bedrock adapter
  fixture suites; 39/39 pre-existing runs still load, export and stay labelled reference-only;
  strict typecheck + Vite build; Compose build and health; smoke_api --restart; all four browser
  scripts; no secrets or content in logs.
Failed: one real bug found and fixed - empty AWS_* values forwarded by Compose made botocore raise
  ProfileNotFound before the credential chain ran. Fixed in kdaa_api/engines.py with a regression test.
Blocked / not run: real Bedrock inference (no AWS credentials, CLI or ~/.aws on this laptop; nothing
  guessed, no credential requested); live token/cost figures; GitHub Actions; AWS infrastructure.
Next concrete action: user supplies Bedrock credentials and a model ID via the single setup step
  above, then one live run is executed and this ledger updated with the real result.
```

```text
Date / environment: 2026-09-13 (live verification), macOS 15.7.9 arm64, Docker 29.4.3 / Compose 5.1.3,
  origin https://github.com/omicsai-lab/kdaa-app.git, HEAD and origin/main at 2cd0b6a (CI passed for
  that commit); all changes below uncommitted; AWS CLI 2.36.44, profile `kdaa` (aws login session)
Changed files and reason:
  src/kdaa/core/llm_engine.py    - amplification no longer substitutes the candidate's full evidence
                                   list for missing/invalid grounding references; valid ids preserved
                                   and de-duplicated, unresolvable ones recorded
  src/kdaa/domain.py             - ArtifactDraft.unresolved_evidence_refs (optional, schema 2.0)
  src/kdaa/core/service.py       - draft.grounding_unresolved provenance event
  src/kdaa/core/reports.py       - Markdown export states an ungrounded draft and lists bad refs
  apps/web/src/{App.tsx,types.ts}- render unresolved references; no false "Grounded in N" line
  tests/test_llm_engine.py       - 6 new grounding tests; corrected one assertion that had encoded
                                   the bug
  scripts/browser_live_run.py    - new: one real live workflow through the UI, --allow-paid-call
  scripts/make_live_ui_fixture.py, scripts/browser_live_ui.py - fixture and UI check now cover a
                                   valid and an unresolvable grounding reference
  compose.llm.yml, .env.example, README.md, docs/TESTING.md - the read-only-mount limitation for
                                   `aws login` profiles, and AWS_REGION for the credential provider
  docs/openapi.json, SOURCE_SHA256SUMS.txt, docs/BUILD_STATUS.md - regenerated / recorded
Commands actually executed:
  aws sts get-caller-identity --profile kdaa --region us-east-1
  aws bedrock list-inference-profiles --region us-east-1 --profile kdaa
  aws bedrock-runtime converse ... (5-token probes: haiku-4-5, nova-lite, nova-2-lite, nova-micro)
  aws login --profile kdaa --region us-east-1          (performed by the maintainer; session had expired)
  eval "$(aws configure export-credentials --profile kdaa --region us-east-1 --format env)"
  docker compose up -d --build ; docker compose -f docker-compose.yml -f compose.llm.yml config
  docker compose -p kdaa-app-tests -f compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
  docker run --rm -v <web>:/web -w /web node:24-bookworm-slim sh -c 'npm ci && npm run build'
  .venv-browser/bin/python scripts/browser_live_run.py --allow-paid-call
  .venv-browser/bin/python scripts/browser_{smoke,uploads,stale_state,live_ui}.py
  python3 scripts/smoke_api.py --base http://127.0.0.1:8183 --restart
  shasum -a 256 -c SOURCE_SHA256SUMS.txt
Passed: 130/130 container tests with real PostgreSQL (0 skipped); one real Bedrock workflow
  (us.amazon.nova-lite-v1:0, us-east-1, 4 calls, 5,036 tokens, 12.6 s, 2 candidates, 4 exact
  quotations, 2 drafts, 0 rejections); strict typecheck + Vite build; all four browser scripts;
  smoke_api --restart; manifest verification (101 files OK).
Failed: the amplification grounding fallback (found by reading, fixed, 5 of 6 new tests confirmed
  failing pre-fix). Two live runs failed before the credential setup was understood: an `aws login`
  profile cannot refresh through the read-only ~/.aws mount, and a profile without a `region` key
  raises NoRegionError that surfaces as a wrong-model error.
Blocked / not run: a live run against an Anthropic model (account-level Bedrock use-case form not
  submitted; Nova Lite used instead); live cost figures (Bedrock returns usage, not price); remote
  GitHub Actions for these changes (they are uncommitted; CI passed for 2cd0b6a); all AWS
  infrastructure (out of scope).
Next concrete action: maintainer reviews the working tree and performs Git operations manually. No
  commit, push, merge or tag was made.
```
