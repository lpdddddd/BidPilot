"""Add deterministic compliance rule engine tables.

Revision ID: g2b6c7d8e9f0
Revises: f1a5b6c7d8e9
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "g2b6c7d8e9f0"
down_revision: str | None = "f1a5b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for stmt in (
        """
        DO $$ BEGIN
            CREATE TYPE compliance_severity AS ENUM (
                'info',
                'warning',
                'error',
                'critical'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """,
        """
        DO $$ BEGIN
            CREATE TYPE compliance_finding_status AS ENUM (
                'pass',
                'fail',
                'unknown'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """,
        """
        DO $$ BEGIN
            CREATE TYPE compliance_rule_category AS ENUM (
                'coverage',
                'evidence',
                'qualification_risk',
                'draft_safety',
                'consistency',
                'engine'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """,
    ):
        op.execute(stmt)

    compliance_severity = postgresql.ENUM(
        "info",
        "warning",
        "error",
        "critical",
        name="compliance_severity",
        create_type=False,
    )
    compliance_finding_status = postgresql.ENUM(
        "pass",
        "fail",
        "unknown",
        name="compliance_finding_status",
        create_type=False,
    )
    compliance_rule_category = postgresql.ENUM(
        "coverage",
        "evidence",
        "qualification_risk",
        "draft_safety",
        "consistency",
        "engine",
        name="compliance_rule_category",
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

    op.create_table(
        "compliance_runs",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("status", extraction_run_status, nullable=False),
        sa.Column("draft_id", sa.UUID(), nullable=True),
        sa.Column(
            "total_checks",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "passed_checks",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "finding_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "severity_counts_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "category_counts_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "rule_ids_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "config_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
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
            name=op.f("fk_compliance_runs_project_id_bid_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["proposal_drafts.id"],
            name=op.f("fk_compliance_runs_draft_id_proposal_drafts"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compliance_runs")),
    )
    op.create_index(
        "ix_compliance_runs_project_id", "compliance_runs", ["project_id"], unique=False
    )
    op.create_index(
        "ix_compliance_runs_status", "compliance_runs", ["status"], unique=False
    )
    op.create_index(
        "uq_compliance_runs_idempotency",
        "compliance_runs",
        ["project_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "compliance_findings",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("finding_id", sa.String(length=256), nullable=False),
        sa.Column("rule_id", sa.String(length=128), nullable=False),
        sa.Column("rule_name", sa.String(length=256), nullable=False),
        sa.Column("category", compliance_rule_category, nullable=False),
        sa.Column("severity", compliance_severity, nullable=False),
        sa.Column("status", compliance_finding_status, nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("remediation", sa.Text(), nullable=True),
        sa.Column("requirement_id", sa.UUID(), nullable=True),
        sa.Column("match_id", sa.UUID(), nullable=True),
        sa.Column("draft_id", sa.UUID(), nullable=True),
        sa.Column(
            "evidence_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "source_location_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["bid_projects.id"],
            name=op.f("fk_compliance_findings_project_id_bid_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["compliance_runs.id"],
            name=op.f("fk_compliance_findings_run_id_compliance_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
            name=op.f("fk_compliance_findings_requirement_id_requirements"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["requirement_evidence_matches.id"],
            name=op.f(
                "fk_compliance_findings_match_id_requirement_evidence_matches"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["proposal_drafts.id"],
            name=op.f("fk_compliance_findings_draft_id_proposal_drafts"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compliance_findings")),
    )
    op.create_index(
        "ix_compliance_findings_project_id",
        "compliance_findings",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_compliance_findings_run_id",
        "compliance_findings",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_compliance_findings_rule_id",
        "compliance_findings",
        ["rule_id"],
        unique=False,
    )
    op.create_index(
        "ix_compliance_findings_severity",
        "compliance_findings",
        ["severity"],
        unique=False,
    )
    op.create_index(
        "ix_compliance_findings_category",
        "compliance_findings",
        ["category"],
        unique=False,
    )
    op.create_index(
        "uq_compliance_findings_run_finding_id",
        "compliance_findings",
        ["run_id", "finding_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_compliance_findings_run_finding_id",
        table_name="compliance_findings",
    )
    op.drop_index("ix_compliance_findings_category", table_name="compliance_findings")
    op.drop_index("ix_compliance_findings_severity", table_name="compliance_findings")
    op.drop_index("ix_compliance_findings_rule_id", table_name="compliance_findings")
    op.drop_index("ix_compliance_findings_run_id", table_name="compliance_findings")
    op.drop_index("ix_compliance_findings_project_id", table_name="compliance_findings")
    op.drop_table("compliance_findings")

    op.drop_index("uq_compliance_runs_idempotency", table_name="compliance_runs")
    op.drop_index("ix_compliance_runs_status", table_name="compliance_runs")
    op.drop_index("ix_compliance_runs_project_id", table_name="compliance_runs")
    op.drop_table("compliance_runs")

    op.execute("DROP TYPE IF EXISTS compliance_rule_category")
    op.execute("DROP TYPE IF EXISTS compliance_finding_status")
    op.execute("DROP TYPE IF EXISTS compliance_severity")
