# KDAA App

**Knowledge Discovery, Assessment, and Amplification.** A local, source-linked reference application, with a Web client and a separately callable Python Core.

**Status: local integration complete and verified on a laptop, and one real Bedrock workflow has now been executed from the Web UI.** 130 tests pass against real PostgreSQL (124 before this milestone; 6 were added with the amplification-grounding fix), and the Docker stack, browser workflow, exports and restart persistence were all executed. One live run against `us.amazon.nova-lite-v1:0` in `us-east-1` completed in 12.6 s over 4 model calls and 5,036 tokens, producing 2 candidates, 4 exact verified quotations, 2 drafted artifacts and 0 rejected quotations. GitHub Actions CI **passed for the initial commit `2cd0b6a`** on `origin/main`; the changes described above are still uncommitted and have **not** run in remote CI. See [BUILD_STATUS](docs/BUILD_STATUS.md) for exact passed/failed/blocked checks.

## What it does

Create a workspace, upload documents, run a bounded discovery/assessment/amplification pipeline, inspect exact source quotations, revisit saved runs, and export JSON or Markdown. Sources and candidate knowledge assets are different entities. Every candidate is provisional; ownership remains unresolved. Repeated copies of a source are retained but are not counted as independent support.

Two engines are available and you choose per run in the UI.

**Reference** (`reference-rules-v0.1`, the default) is transparent, deterministic English-language rules over the uploaded text. No network call, no cost.

**Live AI** (`live-converse-v0.1`) sends the selected documents to one configurable Amazon Bedrock model through the official Converse API, in three bounded stages: discovery, assessment, and a drafted artifact per candidate. It is off until you enable it explicitly, and it makes billable calls when on. See [Live AI mode](#live-ai-mode).

Neither engine is the frozen Paper B implementation or a validated assessment instrument, and neither produces a score, ranking or confidence value. In both modes every quotation is resolved to an exact offset in the stored normalized source by the backend, which never trusts a model-supplied offset; unresolved or ambiguous quotations are rejected and listed with their reason. Generated drafts are proposed content, clearly separated from source-supported facts. The UI displays these limitations. The demo contains fictional source documents, not precomputed result cards. See [the reference-engine description](docs/REFERENCE_ENGINE.md).

## Start locally

Open this folder separately from the other KDAA prototype. Prerequisites for the container path are **Docker Engine/Desktop with Compose v2**, running and usable; no host Python or Node installation is required to run the app.

```bash
docker compose up --build
```

| Service | Local address |
| --- | --- |
| Web | http://localhost:5183 |
| API readiness | http://localhost:8183/ready |
| Offline API reference | http://localhost:8183/docs |
| OpenAPI | http://localhost:8183/openapi.json |

Create a workspace, click **Load demo**, then **Run KDAA**. Explore Discovery, Assessment, Amplification and Provenance. Click an evidence link to see its exact quotation and source locator. Upload your own supported files to obtain different results.

First build downloads base images and dependencies. `apps/web/package-lock.json` is committed, so the frontend image builds reproducibly with `npm ci`.

The `.env.example` is optional; defaults work without copying it. To avoid a port collision, create `.env` and change `WEB_PORT` and `API_PORT`. Reference mode requires no AWS credentials, no LLM subscription and no paid inference call.

## Live AI mode

Live mode is **off by default** and makes **billable Bedrock calls** when enabled. Nothing is enabled implicitly: without all three settings below the Bedrock client is never constructed.

1. Pick a Converse-capable model or inference profile your account can actually use. Do not guess an ID:

   ```bash
   aws bedrock list-inference-profiles --region us-east-1
   ```

2. Create an untracked `.env` (see `.env.example`):

   ```bash
   KDAA_LLM_ENABLED=1
   BEDROCK_REGION=us-east-1
   BEDROCK_MODEL_ID=<the id from step 1>
   ```

3. Provide credentials through the standard AWS chain — never in code, never in Git. Also set `AWS_REGION`: `BEDROCK_REGION` configures the Bedrock client, but the credential provider resolves its own region, and a profile with no `region` key fails with `NoRegionError` that can only surface as an unrecognised model ID.

   Either set `AWS_PROFILE` in `.env` and share your profile read-only:

   ```bash
   docker compose -f docker-compose.yml -f compose.llm.yml up -d --build
   ```

   or hand the container short-lived environment credentials and use the ordinary `docker compose up -d --build`. A profile created by `aws login` **must** use this second path: it refreshes its token by writing to `~/.aws/login/cache`, which the read-only mount forbids. Leave `AWS_PROFILE` unset and export them into the shell, so no secret is written to disk:

   ```bash
   aws login --profile <name> --region <region>      # if the session has expired
   eval "$(aws configure export-credentials --profile <name> --region <region> --format env)"
   docker compose up -d --build
   ```

4. Confirm the server offers it, then pick **Live AI** beside **Run KDAA**:

   ```bash
   curl -s http://localhost:8183/api/v1/system | grep -o '"live_llm":[a-z]*'
   ```

The identity you use needs `bedrock:InvokeModel` for that model, and the model must be enabled for your account in that region. Requesting live mode when it is not configured is refused with `mode_unavailable`; it is never quietly downgraded to reference mode, and a failed live run never becomes a reference result.

Bounds are enforced before any call: 120,000 input characters, 4,000 output tokens, 8 candidates, 12 model calls per run, a 60-second timeout and 3 total SDK attempts (the initial request included). All are configurable in `.env`. Nothing is truncated silently — an oversized selection fails the run and tells you so.

Prompts and raw model responses are written to the source-asset volume under `llm/<run-id>/`, not to the database, to Git or to the container logs. The run configuration, result bundle and provenance record the engine, provider, model, region, prompt versions, input/config hashes, token usage, stop reasons, every rejected quotation and every failure.

Stop with Ctrl-C or `docker compose down`. Restart with `docker compose up --build`. **Do not use `docker compose down -v`** unless you deliberately intend to delete the app's database and source volumes. The app has no delete/reset UI in this version.

## Inputs and bounds

TXT and Markdown must be UTF-8. PDF support is **text-based PDF only**; encrypted PDFs, image-only scans and OCR are not supported. DOCX support extracts body paragraphs and tables, not complete rendered layout. Files are limited to 5 MiB and 100,000 normalized characters; a run accepts at most 20 selected documents and 250,000 characters. PDF extraction is capped at 100 pages.

Original bytes and normalized UTF-8 text are stored separately. Both have SHA-256 identifiers. Evidence locators are zero-based, end-exclusive **Unicode code-point offsets in stored normalized text**, not PDF page coordinates or JavaScript string offsets.

## Layout

```text
apps/web/                 React / TypeScript / Vite client
services/api/kdaa_api/     FastAPI transport, settings and startup
src/kdaa/core/            KDAA service boundary, reference rules, live engine,
                          versioned prompts and quotation resolution
src/kdaa/providers/       Storage ports, local files, PostgreSQL, LLM contract, Bedrock adapter
src/kdaa/domain.py        Typed domain objects and result validation (schema 1.0 and 2.0)
migrations/              Explicit Alembic schema migration
data/demo/                Fictional source fixtures
scripts/                  Environment, HTTP and browser checks
tests/                    Core, parser, API, contracts and real PostgreSQL tests
docs/                     Architecture, status and development guide
```

There is one API process and bounded synchronous execution, not a queue. A live run holds the same single run lock, so one model-backed run happens at a time. PostgreSQL persists metadata/results/provenance; a named volume holds source bytes and normalized text. The Core can be called without FastAPI. React contains no assessment rules.

## Verify

The isolated test stack uses a separate, disposable PostgreSQL test database and no application volumes:

```bash
docker compose -p kdaa-app-tests -f compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
docker compose -p kdaa-app-tests -f compose.test.yml down
```

With the application already running, an available host Python can check real HTTP and non-destructive backend-restart persistence:

```bash
python3 scripts/smoke_api.py --base http://127.0.0.1:8183 --restart
```

A browser acceptance script and exact instructions are in [TESTING](docs/TESTING.md). A successful unit suite alone does **not** prove PostgreSQL migrations, Vite, nginx, containers or restart persistence.

## Safety and scope

Live mode sends your selected document text to Amazon Bedrock. Use non-sensitive, fictional material unless you have cleared the data for that service. Reference mode sends nothing anywhere.

This is an **unauthenticated, single-user localhost PoC**. Published ports bind only to `127.0.0.1`; the database has no published port. Do not expose it to a LAN, internet, tunnel or shared cloud host. The local database password is deliberately nonsecret and must not be reused elsewhere. Use fictional/non-sensitive data for initial testing.

The ledger is application-controlled provenance, **not tamper-proof storage** and not proof that a claim is true, owned, useful or valuable. Uploads are never executed; binary parsers have time/resource limits. In reference mode no source content is sent to an LLM or external service at runtime. In live mode the selected document text is sent to Bedrock by design. Dependency/container downloads during setup do require network access.

AWS **infrastructure** (ECS/Fargate, RDS, S3, Cognito, Terraform), authentication/resource authorization, MCP, agent orchestration, background jobs and native iOS are **not implemented**. Bedrock inference is implemented as an optional local provider only. The future target remains ECR/ECS Fargate + RDS/S3/Bedrock, not individually managed EC2 or Kubernetes. A future iOS client can share the API; its native UI still requires implementation. See [architecture](docs/ARCHITECTURE.md) and [next steps](docs/ROADMAP.md).

## Version control

This checkout tracks `origin` at <https://github.com/omicsai-lab/kdaa-app.git>, with `HEAD` and
`origin/main` at `2cd0b6a`. GitHub Actions CI passed for that commit. Credentials and `.env` are
never tracked.

Git operations are performed manually; nothing in this repository's tooling commits, pushes, merges
or tags on your behalf. Review each step before you run it:

```bash
git status
git diff
git add -A
git commit                 # write the message in your editor
git push origin <your-branch>
```

Do not point this checkout at the other prototype or at a frozen scientific repository.
No software license has been selected yet; choose one before any public release.

See [the development guide](docs/DEVELOPMENT.md) for the project boundaries, how to verify a change locally, and the manual Git steps for publishing one.
