"""Add requirement_extraction_runs table and extraction_run_status enum.

Revision ID: c8d2e3f4a5b6
Revises: b7c1d2e3f4a5
Create Date: 2026-07-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c8d2e3f4a5b6"
down_revision: Union[str, None] = "b7c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum once; table column must reference it with create_type=False
    # to avoid DuplicateObject from a second CREATE TYPE.
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE extraction_run_status AS ENUM (
                'queued', 'running', 'succeeded', 'failed', 'cancelled'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )
    extraction_run_status = postgresql.ENUM(
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        name="extraction_run_status",
        create_type=False,
    )

    op.create_table(
        "requirement_extraction_runs",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("status", extraction_run_status, nullable=False),
        sa.Column("document_ids_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("document_types_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("total_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("merged_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conflict_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["bid_projects.id"],
            name=op.f("fk_requirement_extraction_runs_project_id_bid_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requirement_extraction_runs")),
    )
    op.create_index(
        "ix_requirement_extraction_runs_project_id",
        "requirement_extraction_runs",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_requirement_extraction_runs_status",
        "requirement_extraction_runs",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_requirement_extraction_runs_status",
        table_name="requirement_extraction_runs",
    )
    op.drop_index(
        "ix_requirement_extraction_runs_project_id",
        table_name="requirement_extraction_runs",
    )
    op.drop_table("requirement_extraction_runs")
    op.execute("DROP TYPE IF EXISTS extraction_run_status")
