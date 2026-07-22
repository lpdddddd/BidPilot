"""Add requirement match runs and company evidence match tables.

Revision ID: d9e3f4a5b6c7
Revises: c8d2e3f4a5b6
Create Date: 2026-07-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d9e3f4a5b6c7"
down_revision: Union[str, None] = "c8d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE evidence_match_status AS ENUM (
                'supported',
                'partially_supported',
                'insufficient_evidence',
                'conflicting_evidence',
                'not_applicable'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )
    evidence_match_status = postgresql.ENUM(
        "supported",
        "partially_supported",
        "insufficient_evidence",
        "conflicting_evidence",
        "not_applicable",
        name="evidence_match_status",
        create_type=False,
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
    risk_level = postgresql.ENUM(
        "low",
        "medium",
        "high",
        "critical",
        name="risk_level",
        create_type=False,
    )

    op.create_table(
        "requirement_match_runs",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("status", extraction_run_status, nullable=False),
        sa.Column("requirement_ids_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("document_ids_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("document_types_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("total_requirements", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_requirements", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("partial_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing_evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conflict_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_requirement_count", sa.Integer(), nullable=False, server_default="0"),
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
            name=op.f("fk_requirement_match_runs_project_id_bid_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requirement_match_runs")),
    )
    op.create_index(
        "ix_requirement_match_runs_project_id",
        "requirement_match_runs",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_requirement_match_runs_status",
        "requirement_match_runs",
        ["status"],
        unique=False,
    )

    op.create_table(
        "requirement_evidence_matches",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("requirement_id", sa.UUID(), nullable=False),
        sa.Column("status", evidence_match_status, nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("risk_level", risk_level, nullable=False),
        sa.Column("primary_company_document_id", sa.UUID(), nullable=True),
        sa.Column("primary_company_chunk_id", sa.UUID(), nullable=True),
        sa.Column("primary_company_quote", sa.Text(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            name=op.f("fk_requirement_evidence_matches_project_id_bid_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
            name=op.f("fk_requirement_evidence_matches_requirement_id_requirements"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["primary_company_document_id"],
            ["documents.id"],
            name=op.f("fk_requirement_evidence_matches_primary_company_document_id_documents"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["primary_company_chunk_id"],
            ["document_chunks.id"],
            name=op.f("fk_requirement_evidence_matches_primary_company_chunk_id_document_chunks"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requirement_evidence_matches")),
    )
    op.create_index(
        "ix_requirement_evidence_matches_project_id",
        "requirement_evidence_matches",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_requirement_evidence_matches_requirement_id",
        "requirement_evidence_matches",
        ["requirement_id"],
        unique=False,
    )
    op.create_index(
        "ix_requirement_evidence_matches_status",
        "requirement_evidence_matches",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_requirement_evidence_matches_risk_level",
        "requirement_evidence_matches",
        ["risk_level"],
        unique=False,
    )

    op.create_table(
        "requirement_evidence_match_links",
        sa.Column("match_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=True),
        sa.Column("chunk_id", sa.UUID(), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("role", sa.String(length=64), nullable=False),
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
            ["match_id"],
            ["requirement_evidence_matches.id"],
            name=op.f("fk_requirement_evidence_match_links_match_id_requirement_evidence_matches"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_requirement_evidence_match_links_document_id_documents"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["document_chunks.id"],
            name=op.f("fk_requirement_evidence_match_links_chunk_id_document_chunks"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requirement_evidence_match_links")),
    )
    op.create_index(
        "ix_requirement_evidence_match_links_match_id",
        "requirement_evidence_match_links",
        ["match_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_requirement_evidence_match_links_match_id",
        table_name="requirement_evidence_match_links",
    )
    op.drop_table("requirement_evidence_match_links")
    op.drop_index(
        "ix_requirement_evidence_matches_risk_level",
        table_name="requirement_evidence_matches",
    )
    op.drop_index(
        "ix_requirement_evidence_matches_status",
        table_name="requirement_evidence_matches",
    )
    op.drop_index(
        "ix_requirement_evidence_matches_requirement_id",
        table_name="requirement_evidence_matches",
    )
    op.drop_index(
        "ix_requirement_evidence_matches_project_id",
        table_name="requirement_evidence_matches",
    )
    op.drop_table("requirement_evidence_matches")
    op.drop_index(
        "ix_requirement_match_runs_status",
        table_name="requirement_match_runs",
    )
    op.drop_index(
        "ix_requirement_match_runs_project_id",
        table_name="requirement_match_runs",
    )
    op.drop_table("requirement_match_runs")
    op.execute("DROP TYPE IF EXISTS evidence_match_status")
