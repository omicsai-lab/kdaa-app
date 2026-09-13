"""Initial local reference schema. Explicit migration; do not import mutable app metadata."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
revision = "0001_reference"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("workspaces", sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("record", pg.JSONB, nullable=False))
    op.create_table("documents", sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("record", pg.JSONB, nullable=False))
    op.create_index("ix_documents_workspace_id", "documents", ["workspace_id"])
    op.create_table("runs", sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("record", pg.JSONB, nullable=False),
        sa.UniqueConstraint("workspace_id", "id", name="uq_runs_workspace_id"),
        sa.CheckConstraint("status IN ('pending','running','succeeded','failed','interrupted')", name="ck_run_status"))
    op.create_index("ix_runs_workspace_id", "runs", ["workspace_id"])
    op.create_table("provenance_events", sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("run_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False), sa.Column("record", pg.JSONB, nullable=False),
        sa.ForeignKeyConstraint(["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"], name="fk_event_run_workspace"))
    op.create_index("ix_provenance_events_workspace_id", "provenance_events", ["workspace_id"])
    op.create_index("ix_provenance_events_run_id", "provenance_events", ["run_id"])

def downgrade():
    op.drop_table("provenance_events")
    op.drop_table("runs")
    op.drop_table("documents")
    op.drop_table("workspaces")
