# Development guide

How to work on this repository: the engineering boundaries that must hold, how to verify a change
locally, and the manual Git steps for publishing one.

## Project boundaries

Read `README.md`, `docs/BUILD_STATUS.md`, `docs/ARCHITECTURE.md` and `docs/REFERENCE_ENGINE.md`
before changing behaviour. This is an existing implementation to extend, not a specification to
regenerate.

- Keep domain behaviour in `src/kdaa/core`; the API, the web client and the adapters call it.
  Storage adapters perform I/O on the Core's behalf. "The Core mediates access" does not mean the
  Core contains SQL.
- Keep source documents distinct from provisional knowledge-asset hypotheses. Never infer confirmed
  ownership, proven capability, calibrated confidence or Paper B equivalence from any engine's
  output.
- Preserve exact, hash-linked source evidence. Validate scope, source identity, offsets and quotes
  before persisting results. Duplicate text is not independent evidence, and a model-supplied offset
  is never trusted.
- Persist a completed run and its terminal provenance events in one metadata transaction. There is
  no silent SQLite or in-memory application fallback; the in-memory store lives under `tests/` only.
- Do not remove localhost bindings, origin/host checks, parser bounds or test assertions to make a
  check pass. Do not disable operating-system or browser security policies.
- Keep credentials, real source documents, private exports, runtime volumes and dependency
  directories out of version control. Avoid `sudo` and destructive actions outside this checkout,
  and do not change global Python or Node environments.
- Never run `docker compose down -v`, prune Docker resources or delete user data as a
  troubleshooting shortcut.

Scope discipline: keep changes small and reviewable. Avoid a broad refactor without a concrete
failing behaviour or a contract reason, and preserve package pins unless a real compatibility or
security issue requires a minimal, documented correction. Generate dependency lockfiles from a real
registry; never hand-write one.

## Prerequisites

Docker Engine or Docker Desktop with Compose v2, running. No host Python or Node installation is
required to run the application or its container test suite. A host Python 3.12/3.13 is optional and
only used by `scripts/smoke_api.py` and the browser scripts.

Default ports are 5183 (web) and 8183 (API), and the Compose project name is `kdaa-app`. Check for a
conflict before starting; to change them, copy `.env.example` to `.env` and set `WEB_PORT` and
`API_PORT`.

## Everyday loop

```bash
docker compose up --build -d
docker compose ps                     # wait for all three services to report healthy
docker compose logs --tail=100 backend frontend postgres
```

Verify `http://localhost:8183/ready`, `http://localhost:8183/docs` and `http://localhost:5183`.

Stop with `docker compose down`. **Never add `-v`**: that deletes the database and source volumes.

## Verifying a change

The container suite is the acceptance gate. It starts a disposable PostgreSQL instance in tmpfs,
uses no application volumes, and runs every test including the real-PostgreSQL integration tests:

```bash
docker compose -p kdaa-app-tests -f compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
docker compose -p kdaa-app-tests -f compose.test.yml down
```

`KDAA_REQUIRE_POSTGRES_TESTS=1` makes a missing test database a configuration error rather than a
silent skip. A real driver or server failure must fail; do not mock it away or hide it behind a skip.

Front-end changes need the strict type check and the production build:

```bash
cd apps/web && npm ci && npm run build     # tsc --noEmit, then vite build
```

`docs/TESTING.md` covers the rest: the HTTP and restart-persistence script, the browser acceptance
scripts, the upload matrix, and what the provider-fixture tests do and do not prove.

A passing unit suite alone does not demonstrate that migrations, Vite, nginx, containers or restart
persistence work. Record what you actually ran.

## Recording results

After a meaningful milestone, update `docs/BUILD_STATUS.md` with the exact commands, versions and
outcomes. Label every check **passed**, **failed**, **blocked** or **not run**. Do not present a
mocked or in-process test as equivalent to PostgreSQL or browser-over-HTTP acceptance, and do not
describe a provider-fixture result as a live model result.

## Publishing a change

Git operations are performed manually by the maintainer. Nothing in this repository's tooling
commits, pushes, merges or tags on your behalf.

When a change is ready and its checks pass, review and run the steps yourself:

```bash
git status
git diff
git add -A
git commit              # write the message in your editor
git push origin <your-branch>
```

Open a pull request through your hosting provider's own interface when you want one. Keep the
working tree dirty until you have reviewed the diff.
