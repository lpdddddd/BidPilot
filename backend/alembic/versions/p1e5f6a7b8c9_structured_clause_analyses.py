"""Add structured_clause_analyses persistence (Step 14).

Revision ID: p1e5f6a7b8c9
Revises: o0d4e5f6a7b8
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "p1e5f6a7b8c9"
down_revision: str | None = "o0d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "structured_clause_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("clause_text", sa.Text(), nullable=False),
        sa.Column("raw_output", sa.Text(), nullable=False),
        sa.Column("parsed_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("schema_valid", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "required_field_coverage",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("missing_fields_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("parse_error", sa.String(length=512), nullable=True),
        sa.Column("requested_model_id", sa.String(length=128), nullable=False),
        sa.Column("resolved_model_id", sa.String(length=128), nullable=True),
        sa.Column("served_model_name", sa.String(length=256), nullable=True),
        sa.Column("model_type", sa.String(length=32), nullable=True),
        sa.Column("adapter_version", sa.String(length=64), nullable=True),
        sa.Column("dataset_version", sa.String(length=128), nullable=True),
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("latency_ms", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["project_id"], ["bid_projects.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_structured_clause_analyses_project_id",
        "structured_clause_analyses",
        ["project_id"],
    )
    op.create_index(
        "ix_structured_clause_analyses_task_type",
        "structured_clause_analyses",
        ["task_type"],
    )
    op.create_index(
        "ix_structured_clause_analyses_created_at",
        "structured_clause_analyses",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_structured_clause_analyses_created_at", table_name="structured_clause_analyses")
    op.drop_index("ix_structured_clause_analyses_task_type", table_name="structured_clause_analyses")
    op.drop_index("ix_structured_clause_analyses_project_id", table_name="structured_clause_analyses")
    op.drop_table("structured_clause_analyses")
