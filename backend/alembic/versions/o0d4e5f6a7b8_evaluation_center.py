"""Add evaluation center tables (Step 12).

Revision ID: o0d4e5f6a7b8
Revises: n9c3d4e5f6a7
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "o0d4e5f6a7b8"
down_revision: str | None = "n9c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for stmt in (
        """
        DO $$ BEGIN
            CREATE TYPE evaluation_run_status AS ENUM (
                'queued',
                'running',
                'completed',
                'partial',
                'failed',
                'cancelled'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """,
        """
        DO $$ BEGIN
            CREATE TYPE evaluation_case_status AS ENUM (
                'pending',
                'running',
                'passed',
                'failed',
                'error',
                'skipped',
                'cancelled'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """,
        """
        DO $$ BEGIN
            CREATE TYPE evaluation_target_type AS ENUM (
                'deterministic_fake',
                'rag',
                'extraction',
                'matching',
                'compliance',
                'drafting',
                'agent_pipeline'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """,
        """
        DO $$ BEGIN
            CREATE TYPE evaluation_reference_kind AS ENUM (
                'auto_reference',
                'rule_expected',
                'human_gold',
                'no_direct_reference',
                'executed_without_direct_reference',
                'not_applicable',
                'metric_error'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        """,
    ):
        op.execute(stmt)

    evaluation_run_status = postgresql.ENUM(
        "queued",
        "running",
        "completed",
        "partial",
        "failed",
        "cancelled",
        name="evaluation_run_status",
        create_type=False,
    )
    evaluation_case_status = postgresql.ENUM(
        "pending",
        "running",
        "passed",
        "failed",
        "error",
        "skipped",
        "cancelled",
        name="evaluation_case_status",
        create_type=False,
    )
    evaluation_target_type = postgresql.ENUM(
        "deterministic_fake",
        "rag",
        "extraction",
        "matching",
        "compliance",
        "drafting",
        "agent_pipeline",
        name="evaluation_target_type",
        create_type=False,
    )
    evaluation_reference_kind = postgresql.ENUM(
        "auto_reference",
        "rule_expected",
        "human_gold",
        "no_direct_reference",
        "executed_without_direct_reference",
        "not_applicable",
        "metric_error",
        name="evaluation_reference_kind",
        create_type=False,
    )

    op.create_table(
        "evaluation_suites",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("manifest_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("dataset_hash", sa.String(length=128), nullable=False),
        sa.Column("evaluator_profile_version", sa.String(length=64), nullable=False),
        sa.Column("task_family_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            name=op.f("fk_evaluation_suites_project_id_bid_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_suites")),
    )
    op.create_index(
        "ix_evaluation_suites_project_id", "evaluation_suites", ["project_id"], unique=False
    )
    op.create_index(
        "ix_evaluation_suites_name_version",
        "evaluation_suites",
        ["name", "version"],
        unique=False,
    )

    op.create_table(
        "evaluation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suite_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", evaluation_run_status, nullable=False),
        sa.Column("target_type", evaluation_target_type, nullable=False),
        sa.Column(
            "target_config_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("dataset_hash", sa.String(length=128), nullable=False),
        sa.Column("evaluator_version", sa.String(length=64), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False, server_default="42"),
        sa.Column("total_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("safe_error_summary", sa.Text(), nullable=True),
        sa.Column("source_commit_sha", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=256), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("execution_claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("filter_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
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
            name=op.f("fk_evaluation_runs_project_id_bid_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["suite_id"],
            ["evaluation_suites.id"],
            name=op.f("fk_evaluation_runs_suite_id_evaluation_suites"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_runs")),
    )
    op.create_index(
        "ix_evaluation_runs_project_id", "evaluation_runs", ["project_id"], unique=False
    )
    op.create_index("ix_evaluation_runs_suite_id", "evaluation_runs", ["suite_id"], unique=False)
    op.create_index("ix_evaluation_runs_status", "evaluation_runs", ["status"], unique=False)
    op.create_index(
        "uq_evaluation_runs_project_idempotency",
        "evaluation_runs",
        ["project_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "evaluation_case_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evaluation_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_key", sa.String(length=128), nullable=False),
        sa.Column("case_content_hash", sa.String(length=128), nullable=False),
        sa.Column("task_family", sa.String(length=64), nullable=False),
        sa.Column("split", sa.String(length=32), nullable=False),
        sa.Column("status", evaluation_case_status, nullable=False),
        sa.Column("response_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reference_kind", evaluation_reference_kind, nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("hard_gate_failures", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("safe_error_summary", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reference_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
            ["agent_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_evaluation_case_results_agent_run_id_agent_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            ["evaluation_runs.id"],
            name=op.f("fk_evaluation_case_results_evaluation_run_id_evaluation_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_case_results")),
        sa.UniqueConstraint(
            "evaluation_run_id",
            "case_key",
            name="uq_evaluation_case_results_run_case_key",
        ),
    )
    op.create_index(
        "ix_evaluation_case_results_run_id",
        "evaluation_case_results",
        ["evaluation_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_evaluation_case_results_task_family",
        "evaluation_case_results",
        ["task_family"],
        unique=False,
    )
    op.create_index(
        "ix_evaluation_case_results_status",
        "evaluation_case_results",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_evaluation_case_results_split",
        "evaluation_case_results",
        ["split"],
        unique=False,
    )

    op.create_table(
        "evaluation_metric_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_result_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_name", sa.String(length=128), nullable=False),
        sa.Column("metric_version", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("applicable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("weight", sa.Float(), nullable=False, server_default="0"),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("evidence_summary", sa.Text(), nullable=True),
        sa.Column("reference_kind", evaluation_reference_kind, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["case_result_id"],
            ["evaluation_case_results.id"],
            name=op.f("fk_evaluation_metric_results_case_result_id_evaluation_case_results"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_metric_results")),
        sa.UniqueConstraint(
            "case_result_id",
            "metric_name",
            "metric_version",
            name="uq_evaluation_metric_results_case_metric_version",
        ),
    )
    op.create_index(
        "ix_evaluation_metric_results_case_result_id",
        "evaluation_metric_results",
        ["case_result_id"],
        unique=False,
    )
    op.create_index(
        "ix_evaluation_metric_results_metric_name",
        "evaluation_metric_results",
        ["metric_name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_evaluation_metric_results_metric_name", table_name="evaluation_metric_results"
    )
    op.drop_index(
        "ix_evaluation_metric_results_case_result_id",
        table_name="evaluation_metric_results",
    )
    op.drop_table("evaluation_metric_results")

    op.drop_index("ix_evaluation_case_results_split", table_name="evaluation_case_results")
    op.drop_index("ix_evaluation_case_results_status", table_name="evaluation_case_results")
    op.drop_index(
        "ix_evaluation_case_results_task_family", table_name="evaluation_case_results"
    )
    op.drop_index("ix_evaluation_case_results_run_id", table_name="evaluation_case_results")
    op.drop_table("evaluation_case_results")

    op.drop_index("uq_evaluation_runs_project_idempotency", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_status", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_suite_id", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_project_id", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")

    op.drop_index("ix_evaluation_suites_name_version", table_name="evaluation_suites")
    op.drop_index("ix_evaluation_suites_project_id", table_name="evaluation_suites")
    op.drop_table("evaluation_suites")

    op.execute("DROP TYPE IF EXISTS evaluation_reference_kind")
    op.execute("DROP TYPE IF EXISTS evaluation_target_type")
    op.execute("DROP TYPE IF EXISTS evaluation_case_status")
    op.execute("DROP TYPE IF EXISTS evaluation_run_status")
