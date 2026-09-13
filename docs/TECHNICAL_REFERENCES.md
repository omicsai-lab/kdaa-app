# Primary implementation references

Consulted 2026-09-12. These are dependency/engineering references, not evidence validating KDAA's scientific claims.

- Docker Compose health-gated startup: https://docs.docker.com/compose/how-tos/startup-order/
- Docker published-port binding: https://docs.docker.com/engine/network/port-publishing/
- Vite requirements/build workflow: https://vite.dev/guide/
- npm clean installs and lockfiles: https://docs.npmjs.com/cli/v11/commands/npm-ci/
- SQLAlchemy PostgreSQL/psycopg dialect: https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.psycopg
- pypdf text extraction: https://pypdf.readthedocs.io/en/stable/user/extract-text.html
- pypdf 6.18.1 release/security changes: https://pypdf.readthedocs.io/en/stable/meta/CHANGELOG.html
- pypdf published package metadata: https://pypi.org/project/pypdf/6.18.1/
- python-docx documentation: https://python-docx.readthedocs.io/en/latest/
- GitHub checkout action: https://github.com/actions/checkout

`pypdf==6.18.1` was released 2026-09-11 and includes upstream parsing/security fixes. The older 5.9.0 library preinstalled in the generator is **not** the shipped dependency pin. Passing fixture tests against that older installed library does not prove compatibility/security of a fresh install; the pinned release must be re-tested on a fresh install. No statement here claims a complete vulnerability audit or that any version will remain vulnerability-free.
