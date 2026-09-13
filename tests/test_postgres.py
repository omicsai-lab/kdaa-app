"""Real PostgreSQL only. Tests create/drop a random schema in a dedicated *_test DB."""
import os
from pathlib import Path
from uuid import uuid4
import pytest
from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from kdaa.core.service import KDAAService
from kdaa.providers.local import LocalAssetStore
from kdaa.providers.postgres import PostgresMetadataStore, metadata
from kdaa.domain import Run, RunStatus
from .test_core import SOFTWARE

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture
def postgres(tmp_path, monkeypatch):
    url = os.getenv("KDAA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("No KDAA_TEST_DATABASE_URL: real PostgreSQL execution is pending, not simulated.")
    parsed = make_url(url)
    if parsed.drivername != "postgresql+psycopg" or not (parsed.database or "").endswith("_test"):
        pytest.fail("Tests require a PostgreSQL URL whose dedicated database name ends in _test.")
    schema = f"kdaa_test_{uuid4().hex}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("KDAA_TEST_SCHEMA", schema)
    cfg = Config(str(ROOT / "alembic.ini")); cfg.set_main_option("script_location", str(ROOT / "migrations"))
    store = None
    try:
        command.upgrade(cfg, "head")
        store = PostgresMetadataStore(url, connect_args={"options": f"-csearch_path={schema}"})
        yield store, LocalAssetStore(tmp_path / "assets"), url, schema
    finally:
        if store: store.close()
        # Only the exact random schema created by this fixture is removed.
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()

def test_fresh_migration_matches_current_metadata(postgres):
    store, assets, _, _ = postgres
    assert store.ready()
    with store.engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), metadata) == []

def test_real_persistence_survives_new_connection(postgres):
    store, assets, url, schema = postgres
    core = KDAAService(store, assets)
    workspace = core.create_workspace("PostgreSQL persistence")
    doc = core.import_document(workspace.id, "source.txt", SOFTWARE)
    run = core.start_run(workspace.id)
    assert run.status == RunStatus.SUCCEEDED
    store.close()
    other = PostgresMetadataStore(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        reopened = KDAAService(other, assets)
        assert reopened.get_run(run.id) == run
        assert reopened.read_document(doc.id)["text"]
        assert reopened.get_provenance(run.id)[-1].action == "document.read"
    finally:
        other.close()

def test_postgres_run_recovery(postgres):
    store, assets, _, _ = postgres
    core = KDAAService(store, assets)
    w = core.create_workspace("Recovery")
    core.import_document(w.id, "source.txt", SOFTWARE)
    succeeded = core.start_run(w.id)
    pending = Run(workspace_id=w.id, inputs=succeeded.inputs, config=succeeded.config, config_sha256=succeeded.config_sha256)
    with store.transaction() as tx: tx.add_run(pending)
    assert core.recover_interrupted() == 1
    assert core.get_run(pending.id).status == RunStatus.INTERRUPTED
    assert core.get_run(succeeded.id).status == RunStatus.SUCCEEDED
