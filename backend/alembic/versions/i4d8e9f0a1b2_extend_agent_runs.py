"""Extend agent_runs for LangGraph business loop + checkpoints.

Revision ID: i4d8e9f0a1b2
Revises: h3c7d8e9f0a1
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "i4d8e9f0a1b2"
down_revision: str | None = "h3c7d8e9f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Extend agent_run_status enum with blocked / completed_with_warnings.
    op.execute("ALTER TYPE agent_run_status ADD VALUE IF NOT EXISTS 'blocked'")
    op.execute(
        "ALTER TYPE agent_run_status ADD VALUE IF NOT EXISTS 'completed_with_warnings'"
    )

    op.add_column(
        "agent_runs",
        sa.Column("current_node", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "graph_version",
            sa.String(length=64),
            nullable=True,
            server_default="bidpilot-agent-1.0.0",
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "input_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "output_summary_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column("error_code", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("error_summary", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_agent_runs_idempotency_key",
        "agent_runs",
        ["idempotency_key"],
        unique=False,
    )
    op.create_index(
        "uq_agent_runs_project_id_idempotency_key",
        "agent_runs",
        ["project_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "agent_checkpoints",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("agent_run_id", sa.UUID(), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=128), nullable=False),
        sa.Column("node_name", sa.String(length=128), nullable=True),
        sa.Column(
            "checkpoint_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_agent_checkpoints_agent_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_checkpoints")),
        sa.UniqueConstraint(
            "thread_id",
            "checkpoint_id",
            name="uq_agent_checkpoints_thread_id_checkpoint_id",
        ),
    )
    op.create_index(
        op.f("ix_agent_checkpoints_agent_run_id"),
        "agent_checkpoints",
        ["agent_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_checkpoints_thread_id",
        "agent_checkpoints",
        ["thread_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_checkpoints_thread_id", table_name="agent_checkpoints")
    op.drop_index(
        op.f("ix_agent_checkpoints_agent_run_id"), table_name="agent_checkpoints"
    )
    op.drop_table("agent_checkpoints")

    op.drop_index(
        "uq_agent_runs_project_id_idempotency_key", table_name="agent_runs"
    )
    op.drop_index("ix_agent_runs_idempotency_key", table_name="agent_runs")
    op.drop_column("agent_runs", "error_summary")
    op.drop_column("agent_runs", "error_code")
    op.drop_column("agent_runs", "output_summary_json")
    op.drop_column("agent_runs", "input_json")
    op.drop_column("agent_runs", "idempotency_key")
    op.drop_column("agent_runs", "graph_version")
    op.drop_column("agent_runs", "current_node")
