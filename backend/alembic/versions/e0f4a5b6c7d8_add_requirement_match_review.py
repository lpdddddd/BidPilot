"""Add requirement match human review tables and protection columns.

Revision ID: e0f4a5b6c7d8
Revises: d9e3f4a5b6c7
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e0f4a5b6c7d8"
down_revision: str | None = "d9e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE match_review_status AS ENUM (
                'pending',
                'confirmed',
                'rejected',
                'needs_more_material'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE match_review_action AS ENUM (
                'confirm',
                'reject',
                'needs_more_material',
                'reopen'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE match_review_reason_code AS ENUM (
                'evidence_insufficient',
                'evidence_incorrect',
                'status_incorrect',
                'scope_unclear',
                'needs_updated_material',
                'other'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE actor_authn AS ENUM (
                'authenticated',
                'unverified_local_operator'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """
    )

    match_review_status = postgresql.ENUM(
        "pending",
        "confirmed",
        "rejected",
        "needs_more_material",
        name="match_review_status",
        create_type=False,
    )
    match_review_action = postgresql.ENUM(
        "confirm",
        "reject",
        "needs_more_material",
        "reopen",
        name="match_review_action",
        create_type=False,
    )
    match_review_reason_code = postgresql.ENUM(
        "evidence_insufficient",
        "evidence_incorrect",
        "status_incorrect",
        "scope_unclear",
        "needs_updated_material",
        "other",
        name="match_review_reason_code",
        create_type=False,
    )
    actor_authn = postgresql.ENUM(
        "authenticated",
        "unverified_local_operator",
        name="actor_authn",
        create_type=False,
    )

    op.add_column(
        "requirement_match_runs",
        sa.Column(
            "protected_requirement_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "requirement_match_runs",
        sa.Column(
            "skipped_reviewed_requirement_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    op.add_column(
        "requirement_evidence_matches",
        sa.Column(
            "review_status",
            match_review_status,
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "requirement_evidence_matches",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "requirement_evidence_matches",
        sa.Column("reviewed_by", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "requirement_evidence_matches",
        sa.Column(
            "review_lock_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "requirement_evidence_matches",
        sa.Column(
            "is_review_protected",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "requirement_evidence_matches",
        sa.Column(
            "lifecycle_status",
            sa.String(length=32),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "requirement_evidence_matches",
        sa.Column("superseded_by_match_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "requirement_evidence_matches",
        sa.Column("supersedes_match_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        op.f(
            "fk_requirement_evidence_matches_superseded_by_match_id_"
            "requirement_evidence_matches"
        ),
        "requirement_evidence_matches",
        "requirement_evidence_matches",
        ["superseded_by_match_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f(
            "fk_requirement_evidence_matches_supersedes_match_id_"
            "requirement_evidence_matches"
        ),
        "requirement_evidence_matches",
        "requirement_evidence_matches",
        ["supersedes_match_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_requirement_evidence_matches_review_status",
        "requirement_evidence_matches",
        ["review_status"],
        unique=False,
    )
    op.create_index(
        "ix_requirement_evidence_matches_lifecycle_status",
        "requirement_evidence_matches",
        ["lifecycle_status"],
        unique=False,
    )

    # Backfill existing rows for review workflow defaults.
    op.execute(
        """
        UPDATE requirement_evidence_matches
        SET
            review_status = 'pending',
            needs_review = true,
            is_review_protected = false,
            review_lock_version = 0,
            lifecycle_status = 'active'
        WHERE review_status IS NULL
           OR lifecycle_status IS NULL
           OR review_lock_version IS NULL
           OR is_review_protected IS NULL
        """
    )

    op.create_table(
        "requirement_match_reviews",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("match_id", sa.UUID(), nullable=False),
        sa.Column("action", match_review_action, nullable=False),
        sa.Column("from_review_status", match_review_status, nullable=False),
        sa.Column("to_review_status", match_review_status, nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("reason_code", match_review_reason_code, nullable=True),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("actor_label", sa.String(length=128), nullable=False),
        sa.Column("actor_authn", actor_authn, nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
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
            name=op.f("fk_requirement_match_reviews_project_id_bid_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["requirement_evidence_matches.id"],
            name=op.f(
                "fk_requirement_match_reviews_match_id_requirement_evidence_matches"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_requirement_match_reviews")),
    )
    op.create_index(
        "ix_requirement_match_reviews_project_id",
        "requirement_match_reviews",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_requirement_match_reviews_match_id",
        "requirement_match_reviews",
        ["match_id"],
        unique=False,
    )
    op.create_index(
        "uq_requirement_match_reviews_idempotency",
        "requirement_match_reviews",
        ["project_id", "match_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_requirement_match_reviews_idempotency",
        table_name="requirement_match_reviews",
    )
    op.drop_index(
        "ix_requirement_match_reviews_match_id",
        table_name="requirement_match_reviews",
    )
    op.drop_index(
        "ix_requirement_match_reviews_project_id",
        table_name="requirement_match_reviews",
    )
    op.drop_table("requirement_match_reviews")

    op.drop_index(
        "ix_requirement_evidence_matches_lifecycle_status",
        table_name="requirement_evidence_matches",
    )
    op.drop_index(
        "ix_requirement_evidence_matches_review_status",
        table_name="requirement_evidence_matches",
    )
    op.drop_constraint(
        op.f(
            "fk_requirement_evidence_matches_supersedes_match_id_"
            "requirement_evidence_matches"
        ),
        "requirement_evidence_matches",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f(
            "fk_requirement_evidence_matches_superseded_by_match_id_"
            "requirement_evidence_matches"
        ),
        "requirement_evidence_matches",
        type_="foreignkey",
    )
    op.drop_column("requirement_evidence_matches", "supersedes_match_id")
    op.drop_column("requirement_evidence_matches", "superseded_by_match_id")
    op.drop_column("requirement_evidence_matches", "lifecycle_status")
    op.drop_column("requirement_evidence_matches", "is_review_protected")
    op.drop_column("requirement_evidence_matches", "review_lock_version")
    op.drop_column("requirement_evidence_matches", "reviewed_by")
    op.drop_column("requirement_evidence_matches", "reviewed_at")
    op.drop_column("requirement_evidence_matches", "review_status")

    op.drop_column("requirement_match_runs", "skipped_reviewed_requirement_count")
    op.drop_column("requirement_match_runs", "protected_requirement_count")

    op.execute("DROP TYPE IF EXISTS actor_authn")
    op.execute("DROP TYPE IF EXISTS match_review_reason_code")
    op.execute("DROP TYPE IF EXISTS match_review_action")
    op.execute("DROP TYPE IF EXISTS match_review_status")
