"""Add auditable proposal drafting workspace tables.

Revision ID: f1a5b6c7d8e9
Revises: e0f4a5b6c7d8
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f1a5b6c7d8e9"
down_revision: str | None = "e0f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for stmt in (
        """
        DO $$ BEGIN
            CREATE TYPE proposal_draft_status AS ENUM (
                'draft_pending_review',
                'reviewed',
                'reopened',
                'archived'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """,
        """
        DO $$ BEGIN
            CREATE TYPE proposal_draft_version_kind AS ENUM (
                'generated',
                'manual_revision'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """,
        """
        DO $$ BEGIN
            CREATE TYPE proposal_draft_source_role AS ENUM (
                'tender_requirement',
                'company_support',
                'company_conflict',
                'company_scope_exclusion'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """,
        """
        DO $$ BEGIN
            CREATE TYPE proposal_draft_review_action AS ENUM (
                'mark_reviewed',
                'reopen'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """,
        """
        DO $$ BEGIN
            CREATE TYPE proposal_draft_generation_mode AS ENUM (
                'response_outline',
                'compliance_preparation_pack'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """,
    ):
        op.execute(stmt)

    proposal_draft_status = postgresql.ENUM(
        "draft_pending_review",
        "reviewed",
        "reopened",
        "archived",
        name="proposal_draft_status",
        create_type=False,
    )
    proposal_draft_version_kind = postgresql.ENUM(
        "generated",
        "manual_revision",
        name="proposal_draft_version_kind",
        create_type=False,
    )
    proposal_draft_source_role = postgresql.ENUM(
        "tender_requirement",
        "company_support",
        "company_conflict",
        "company_scope_exclusion",
        name="proposal_draft_source_role",
        create_type=False,
    )
    proposal_draft_review_action = postgresql.ENUM(
        "mark_reviewed",
        "reopen",
        name="proposal_draft_review_action",
        create_type=False,
    )
    proposal_draft_generation_mode = postgresql.ENUM(
        "response_outline",
        "compliance_preparation_pack",
        name="proposal_draft_generation_mode",
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
    actor_authn = postgresql.ENUM(
        "authenticated",
        "unverified_local_operator",
        name="actor_authn",
        create_type=False,
    )

    op.create_table(
        "proposal_drafts",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("status", proposal_draft_status, nullable=False),
        sa.Column("current_version_id", sa.UUID(), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column(
            "review_lock_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["bid_projects.id"],
            name=op.f("fk_proposal_drafts_project_id_bid_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposal_drafts")),
    )
    op.create_index(
        "ix_proposal_drafts_project_id", "proposal_drafts", ["project_id"], unique=False
    )
    op.create_index(
        "ix_proposal_drafts_status", "proposal_drafts", ["status"], unique=False
    )

    op.create_table(
        "proposal_draft_generation_runs",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("status", extraction_run_status, nullable=False),
        sa.Column("mode", proposal_draft_generation_mode, nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column(
            "requested_requirement_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "eligible_requirement_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "excluded_requirement_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("excluded_reason_summary", sa.Text(), nullable=True),
        sa.Column("draft_id", sa.UUID(), nullable=True),
        sa.Column("draft_version_id", sa.UUID(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
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
            name=op.f("fk_proposal_draft_generation_runs_project_id_bid_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["proposal_drafts.id"],
            name=op.f("fk_proposal_draft_generation_runs_draft_id_proposal_drafts"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposal_draft_generation_runs")),
    )
    op.create_index(
        "ix_proposal_draft_generation_runs_project_id",
        "proposal_draft_generation_runs",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_proposal_draft_generation_runs_status",
        "proposal_draft_generation_runs",
        ["status"],
        unique=False,
    )
    op.create_index(
        "uq_proposal_draft_generation_runs_idempotency",
        "proposal_draft_generation_runs",
        ["project_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "proposal_draft_versions",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("draft_id", sa.UUID(), nullable=False),
        sa.Column("parent_version_id", sa.UUID(), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("version_kind", proposal_draft_version_kind, nullable=False),
        sa.Column("generation_run_id", sa.UUID(), nullable=True),
        sa.Column("source_snapshot_hash", sa.String(length=128), nullable=True),
        sa.Column(
            "content_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("content_markdown", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("supersedes_version_id", sa.UUID(), nullable=True),
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
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
            name=op.f("fk_proposal_draft_versions_project_id_bid_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["proposal_drafts.id"],
            name=op.f("fk_proposal_draft_versions_draft_id_proposal_drafts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_version_id"],
            ["proposal_draft_versions.id"],
            name=op.f(
                "fk_proposal_draft_versions_parent_version_id_proposal_draft_versions"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_version_id"],
            ["proposal_draft_versions.id"],
            name=op.f(
                "fk_proposal_draft_versions_supersedes_version_id_proposal_draft_versions"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["generation_run_id"],
            ["proposal_draft_generation_runs.id"],
            name="fk_proposal_draft_versions_generation_run_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposal_draft_versions")),
    )
    op.create_index(
        "ix_proposal_draft_versions_project_id",
        "proposal_draft_versions",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_proposal_draft_versions_draft_id",
        "proposal_draft_versions",
        ["draft_id"],
        unique=False,
    )
    op.create_index(
        "uq_proposal_draft_versions_draft_number",
        "proposal_draft_versions",
        ["draft_id", "version_number"],
        unique=True,
    )

    op.create_foreign_key(
        "fk_proposal_drafts_current_version_id",
        "proposal_drafts",
        "proposal_draft_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_proposal_draft_generation_runs_draft_version_id",
        "proposal_draft_generation_runs",
        "proposal_draft_versions",
        ["draft_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "proposal_draft_sources",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("draft_version_id", sa.UUID(), nullable=False),
        sa.Column("requirement_id", sa.UUID(), nullable=True),
        sa.Column("match_id", sa.UUID(), nullable=True),
        sa.Column("evidence_link_id", sa.UUID(), nullable=True),
        sa.Column("source_role", proposal_draft_source_role, nullable=False),
        sa.Column("source_quote", sa.Text(), nullable=True),
        sa.Column(
            "location_json",
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
            name=op.f("fk_proposal_draft_sources_project_id_bid_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["draft_version_id"],
            ["proposal_draft_versions.id"],
            name=op.f(
                "fk_proposal_draft_sources_draft_version_id_proposal_draft_versions"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
            name=op.f("fk_proposal_draft_sources_requirement_id_requirements"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["requirement_evidence_matches.id"],
            name=op.f(
                "fk_proposal_draft_sources_match_id_requirement_evidence_matches"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposal_draft_sources")),
    )
    op.create_index(
        "ix_proposal_draft_sources_project_id",
        "proposal_draft_sources",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_proposal_draft_sources_draft_version_id",
        "proposal_draft_sources",
        ["draft_version_id"],
        unique=False,
    )

    op.create_table(
        "proposal_draft_reviews",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("draft_id", sa.UUID(), nullable=False),
        sa.Column("draft_version_id", sa.UUID(), nullable=False),
        sa.Column("action", proposal_draft_review_action, nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("actor_label", sa.String(length=128), nullable=False),
        sa.Column("actor_authn", actor_authn, nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("payload_hash", sa.String(length=128), nullable=True),
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
            name=op.f("fk_proposal_draft_reviews_project_id_bid_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["proposal_drafts.id"],
            name=op.f("fk_proposal_draft_reviews_draft_id_proposal_drafts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["draft_version_id"],
            ["proposal_draft_versions.id"],
            name=op.f(
                "fk_proposal_draft_reviews_draft_version_id_proposal_draft_versions"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposal_draft_reviews")),
    )
    op.create_index(
        "ix_proposal_draft_reviews_project_id",
        "proposal_draft_reviews",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_proposal_draft_reviews_draft_id",
        "proposal_draft_reviews",
        ["draft_id"],
        unique=False,
    )
    op.create_index(
        "uq_proposal_draft_reviews_idempotency",
        "proposal_draft_reviews",
        ["project_id", "draft_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_proposal_draft_reviews_idempotency",
        table_name="proposal_draft_reviews",
    )
    op.drop_index(
        "ix_proposal_draft_reviews_draft_id",
        table_name="proposal_draft_reviews",
    )
    op.drop_index(
        "ix_proposal_draft_reviews_project_id",
        table_name="proposal_draft_reviews",
    )
    op.drop_table("proposal_draft_reviews")

    op.drop_index(
        "ix_proposal_draft_sources_draft_version_id",
        table_name="proposal_draft_sources",
    )
    op.drop_index(
        "ix_proposal_draft_sources_project_id",
        table_name="proposal_draft_sources",
    )
    op.drop_table("proposal_draft_sources")

    op.drop_constraint(
        "fk_proposal_draft_generation_runs_draft_version_id",
        "proposal_draft_generation_runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_proposal_drafts_current_version_id",
        "proposal_drafts",
        type_="foreignkey",
    )

    op.drop_index(
        "uq_proposal_draft_versions_draft_number",
        table_name="proposal_draft_versions",
    )
    op.drop_index(
        "ix_proposal_draft_versions_draft_id",
        table_name="proposal_draft_versions",
    )
    op.drop_index(
        "ix_proposal_draft_versions_project_id",
        table_name="proposal_draft_versions",
    )
    op.drop_table("proposal_draft_versions")

    op.drop_index(
        "uq_proposal_draft_generation_runs_idempotency",
        table_name="proposal_draft_generation_runs",
    )
    op.drop_index(
        "ix_proposal_draft_generation_runs_status",
        table_name="proposal_draft_generation_runs",
    )
    op.drop_index(
        "ix_proposal_draft_generation_runs_project_id",
        table_name="proposal_draft_generation_runs",
    )
    op.drop_table("proposal_draft_generation_runs")

    op.drop_index("ix_proposal_drafts_status", table_name="proposal_drafts")
    op.drop_index("ix_proposal_drafts_project_id", table_name="proposal_drafts")
    op.drop_table("proposal_drafts")

    op.execute("DROP TYPE IF EXISTS proposal_draft_generation_mode")
    op.execute("DROP TYPE IF EXISTS proposal_draft_review_action")
    op.execute("DROP TYPE IF EXISTS proposal_draft_source_role")
    op.execute("DROP TYPE IF EXISTS proposal_draft_version_kind")
    op.execute("DROP TYPE IF EXISTS proposal_draft_status")
