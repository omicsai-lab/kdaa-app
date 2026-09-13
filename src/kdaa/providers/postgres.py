"""PostgreSQL persistence. Versioned bundles use JSONB; important links use real FKs."""
from contextlib import contextmanager
from uuid import UUID
from sqlalchemy import (Column, DateTime, ForeignKey, ForeignKeyConstraint, MetaData, String,
                        Table, CheckConstraint, UniqueConstraint, create_engine, insert, select, text, update)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from kdaa.domain import Workspace, SourceDocument, Run, ProvenanceEvent
from kdaa.errors import KDAAError

metadata = MetaData()
workspaces = Table("workspaces", metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("record", JSONB, nullable=False))
documents = Table("documents", metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("workspace_id", PGUUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("record", JSONB, nullable=False))
runs = Table("runs", metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("workspace_id", PGUUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, index=True),
    Column("status", String(20), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("record", JSONB, nullable=False),
    UniqueConstraint("workspace_id", "id", name="uq_runs_workspace_id"),
    CheckConstraint("status IN ('pending','running','succeeded','failed','interrupted')", name="ck_run_status"))
events = Table("provenance_events", metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("workspace_id", PGUUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=False, index=True),
    Column("run_id", PGUUID(as_uuid=True), nullable=True, index=True),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("record", JSONB, nullable=False),
    ForeignKeyConstraint(["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"], name="fk_event_run_workspace"))

class PostgresSession:
    def __init__(self, connection):
        self.connection = connection
    def _add(self, table, item, **columns):
        self.connection.execute(insert(table).values(id=item.id, record=item.model_dump(mode="json"), **columns))
    def _get(self, table, id, model):
        raw = self.connection.execute(select(table.c.record).where(table.c.id == id)).scalar_one_or_none()
        return model.model_validate(raw) if raw is not None else None
    def _list(self, statement, model):
        return [model.model_validate(raw) for raw in self.connection.execute(statement).scalars()]
    def add_workspace(self, item: Workspace) -> None:
        self._add(workspaces, item, created_at=item.created_at)
    def get_workspace(self, id: UUID) -> Workspace | None:
        return self._get(workspaces, id, Workspace)
    def list_workspaces(self) -> list[Workspace]:
        return self._list(select(workspaces.c.record).order_by(workspaces.c.created_at, workspaces.c.id), Workspace)
    def add_document(self, item: SourceDocument) -> None:
        self._add(documents, item, workspace_id=item.workspace_id, created_at=item.created_at)
    def get_document(self, id: UUID) -> SourceDocument | None:
        return self._get(documents, id, SourceDocument)
    def list_documents(self, workspace_id: UUID) -> list[SourceDocument]:
        return self._list(select(documents.c.record).where(documents.c.workspace_id == workspace_id)
                          .order_by(documents.c.created_at, documents.c.id), SourceDocument)
    def add_run(self, item: Run) -> None:
        self._add(runs, item, workspace_id=item.workspace_id, status=item.status.value, created_at=item.created_at)
    def update_run(self, item: Run) -> None:
        result = self.connection.execute(update(runs).where(runs.c.id == item.id,
            runs.c.workspace_id == item.workspace_id, runs.c.status.in_(["pending", "running"]))
            .values(status=item.status.value, record=item.model_dump(mode="json")))
        if result.rowcount != 1:
            raise KDAAError("immutable_run", "Finalized runs cannot be overwritten.", 409)
    def get_run(self, id: UUID) -> Run | None:
        return self._get(runs, id, Run)
    def list_runs(self, workspace_id: UUID) -> list[Run]:
        return self._list(select(runs.c.record).where(runs.c.workspace_id == workspace_id)
                          .order_by(runs.c.created_at.desc(), runs.c.id), Run)
    def unfinished_runs(self) -> list[Run]:
        return self._list(select(runs.c.record).where(runs.c.status.in_(["pending", "running"])), Run)
    def add_event(self, item: ProvenanceEvent) -> None:
        self._add(events, item, workspace_id=item.workspace_id, run_id=item.run_id, timestamp=item.timestamp)
    def list_events(self, workspace_id: UUID, run_id: UUID | None = None) -> list[ProvenanceEvent]:
        query = select(events.c.record).where(events.c.workspace_id == workspace_id)
        if run_id is not None:
            query = query.where(events.c.run_id == run_id)
        return self._list(query.order_by(events.c.timestamp, events.c.id), ProvenanceEvent)

class PostgresMetadataStore:
    def __init__(self, database_url: str, *, connect_args: dict | None = None):
        if not database_url.startswith("postgresql+psycopg://"):
            raise ValueError("Use PostgreSQL with the psycopg driver; SQLite is not an application fallback.")
        self.engine = create_engine(database_url, pool_pre_ping=True, connect_args=connect_args or {})
    @contextmanager
    def transaction(self):
        with self.engine.begin() as connection:
            yield PostgresSession(connection)
    def ready(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                connection.execute(select(workspaces.c.id).limit(1))
            return True
        except Exception:
            return False
    def close(self) -> None:
        self.engine.dispose()
