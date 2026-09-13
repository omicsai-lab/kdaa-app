# Application architecture and decisions

## One Core; separate clients

```text
Browser / React                         Future iOS (not implemented)
       |                                           |
       +------------ /api/v1 ----------------------+
                              |
                         FastAPI transport
                              |
                       KDAAService / Core
                        /      |       \
             reference rules  |      asset-storage port
                              |              |
                        metadata port    local files
                              |
                        PostgreSQL
```

The Core is importable without FastAPI. APIs and future clients share typed domain operations; the UI contains presentation and interaction, not discovery/scoring rules. The API version is v1; exported domain records have schema/version information. `docs/openapi.json` is a checked-in snapshot, not a substitute for the live schema after changes.

“Core mediates access” means the Core decides and records knowledge-domain operations. PostgreSQL/file adapters necessarily perform physical I/O on its behalf. Readiness checks, migrations, container health probes and the test harness are not knowledge discovery operations and do not need to masquerade as domain calls. No agent or MCP transport exists yet.

## Data model

`Workspace` establishes a focal unit and optional goal. `SourceDocument` preserves bytes, normalized text hashes and parser metadata. `Run` snapshots the chosen source IDs/hashes and reference-engine configuration. Its `ResultBundle` contains `AssetHypothesis`, `Evidence`, `Assessment` and `Amplification` records. A source may support multiple candidates; uploading a document does not by itself establish an asset.

PostgreSQL stores four minimal tables: workspaces, documents, runs and provenance events. Typed record payloads use JSONB, while scoped identifiers, status, timestamps and foreign keys remain explicit relational columns. A completed run owns an immutable result bundle instead of a large collection of premature relational result tables. Source bytes are not stored in PostgreSQL. Add normalized query tables later only for demonstrated query/reporting needs.

The local file adapter uses generated keys, resolves paths within its root and publishes immutable individual files. Each document stores a raw-byte hash and a normalized-text hash. Filenames are display metadata, not storage paths.

## Transactions and provenance

A run follows `pending -> running -> succeeded | failed | interrupted`. Terminal runs cannot be overwritten by ordinary Core calls. Results and their successful terminal/stage events are committed in one database transaction. A failed write cannot be represented as a successful completed run. Previously active runs are marked interrupted when the one-process application restarts; they are never silently rerun.

File upload and PostgreSQL are not one distributed transaction. The importer publishes files, then stores document metadata/import provenance transactionally; on database failure it removes the new files. Abrupt process/power loss can leave an orphan file. This is an explicit local-PoC limitation, not a claim of cross-store atomicity. No automatic orphan deletion is implemented.

Events include actor, action, workspace/run IDs, sources, evidence, output references, timestamp and relevant configuration. Source content/original downloads, evidence inspection and exports add access events; routine metadata listing/health checks do not append a noise event per HTTP GET. Provenance is application-enforced and queryable, not an external tamper-proof ledger. Local DB administrators can alter it.

## Why no queue or second service

Runs are bounded by source count/text limits and use a deterministic local engine. One nonblocking process lock rejects a concurrent run with a clear busy error. Upload parsing uses a bounded subprocess. The API has **one Uvicorn worker**; do not scale workers until concurrency, recovery and job ownership have been redesigned deliberately.

A future networked, tool-using LLM run may require a durable queue/worker. At that point keep status and idempotency in the Core and treat the queue as transport. Do not add Redis or a worker merely because the target diagram once contained them.

## LLM boundary

`LLMProvider` is text in, text out. Prompt construction, JSON parsing, quote resolution and every domain validation live in the Core, so a provider cannot widen what the application is willing to believe. `StubLLMProvider` accepts explicit test fixtures and always reports `simulated=True`; it never manufactures displayed results. `BedrockConverseProvider` is the only module that imports the AWS SDK.

There are two run modes and no automatic movement between them. `reference` is the deterministic lexical engine and remains the default. `live` uses one configurable Bedrock model through the official Converse API. Live mode appears only when `KDAA_LLM_ENABLED=1` and both `BEDROCK_REGION` and `BEDROCK_MODEL_ID` are set: otherwise the client is never constructed, so an unconfigured deployment cannot make a paid call. Requesting an unavailable mode is refused with `mode_unavailable`; a failing live run fails, and never silently produces a reference result.

Credentials are resolved by the standard boto3 chain and are never read, stored, logged or accepted as a parameter by application code. `/api/v1/system` reports the region and model ID as configuration and reports no credential.

### What the model decides, and what it does not

The model reads the selected documents and proposes candidates, assessments and drafts. It does not decide what becomes evidence. It is never asked for a character offset and is never believed about one: it supplies a document id and a quotation, and `core/quotes.py` finds that quotation in the stored normalized text itself. A quotation that occurs zero times is rejected as unfounded; one that occurs more than once is rejected as ambiguous, because an ambiguous locator is not a stable citation. The only tolerated adjustments are NFC normalization and trimming the ends of the supplied string, both as separate exact searches. Every rejection is recorded with its reason as an `EvidenceRejection` and an `evidence.rejected` provenance event, and a candidate whose quotations all fail is dropped rather than repaired.

Document text is untrusted data. It is passed inside delimited `<document>` blocks and the system prompt states that instructions inside it are content to describe, never commands to follow.

### Versioned result schema

Live results add records that have no lexical equivalent, so they carry `schema_version` 2.0 while reference bundles stay at 1.0. Every 2.0 field is optional with a default, so runs stored before live mode existed parse unchanged and are not reinterpreted: `provider`, `rejections`, `SemanticAssessment`, `ArtifactDraft` and `discovery_basis` all default to their reference meaning. Semantic assessment is a separate record rather than an overload of the lexical fields, which stay `not_assessed`/empty in live mode because no lexical overlap is computed. No score, ranking or confidence value is produced in either mode.

`ArtifactDraft` holds an actual drafted artifact rather than instructions for producing one, and separates `grounded_evidence_ids` from `proposed_elements` so generated content is never presented as source-supported fact.

### Bounds and recorded runtime

Input characters, output tokens, candidate count, total model calls, request timeout and total SDK attempts are all bounded before any call is made. Attempts use botocore's `total_max_attempts`, which counts the initial request, so the SDK cannot retry past the budget. Nothing is truncated silently: an oversized input is refused with `llm_input_too_large`, and a `max_tokens` stop reason is refused rather than accepted as an answer.

Engine, provider, model, region, prompt versions, input/config hashes, token usage, stop reasons, validation outcomes and failures are recorded in the run configuration, the result bundle and provenance. Full request and response bodies are written to the asset volume under generated `llm/<run>/<nn>-<stage>.json` keys, never to the database, to Git or to ordinary logs; only keys and hashes are stored alongside the result.

## Deployment and later clients

The three local containers are nginx/Web, Python/API and PostgreSQL. Health checks gate startup; Alembic runs before API readiness. Published ports bind to localhost; source/database data use named volumes. Frontend and API communicate through same-origin nginx routes by default.

The future target is the same application images on ECS/Fargate, local PostgreSQL -> RDS, local asset storage -> S3, and an explicit Bedrock provider. Cloud networking, credentials, database migration, authentication, resource authorization, retention and background execution remain real work. Those are not “already production-ready” because ports exist.

Future `apps/ios/` may call the same API. Its native screens, file import, login flows and distribution will still need implementation. Do not create an empty iOS project now.

## Security scope

No authentication or multi-user authorization is implemented. Workspace separation is data organization, not a tenant security guarantee. Localhost binding, trusted hosts/origins, safe file identifiers, bounded parsing and no remote runtime resources reduce local exposure but are not production hardening. Before any shared/public deployment, design identity **and per-resource authorization**, secrets, HTTPS, quotas, failure recovery and retention. Do not expose this build through a tunnel.
