"""Add agent_events unified timeline + tool_call linkage fields.

Revision ID: k6f0a1b2c3d4
Revises: j5e9f0a1b2c3
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "k6f0a1b2c3d4"
down_revision: str | None = "j5e9f0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("event_sequence", sa.Integer(), server_default="0", nullable=False),
    )

    op.add_column("tool_calls", sa.Column("call_id", sa.String(length=64), nullable=True))
    op.add_column("tool_calls", sa.Column("node_name", sa.String(length=255), nullable=True))
    op.add_column(
        "tool_calls",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tool_calls",
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_tool_calls_call_id", "tool_calls", ["call_id"])

    op.create_table(
        "agent_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("node_name", sa.String(length=255), nullable=True),
        sa.Column("tool_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("safe_summary", sa.Text(), nullable=True),
        sa.Column("agent_step_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tool_call_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("call_id", sa.String(length=64), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_step_id"], ["agent_steps.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tool_call_id"], ["tool_calls.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent_run_id",
            "sequence",
            name="uq_agent_events_agent_run_id_sequence",
        ),
    )
    op.create_index("ix_agent_events_agent_run_id", "agent_events", ["agent_run_id"])
    op.create_index(
        "ix_agent_events_agent_run_id_sequence",
        "agent_events",
        ["agent_run_id", "sequence"],
    )
    op.create_index("ix_agent_events_event_type", "agent_events", ["event_type"])
    op.create_index("ix_agent_events_agent_step_id", "agent_events", ["agent_step_id"])
    op.create_index("ix_agent_events_tool_call_id", "agent_events", ["tool_call_id"])

    # Deterministic backfill from historical steps + tools (no 10000+ offsets).
    # Order: for each run, node_completed events by step_index, then tool_completed
    # by created_at. Then set event_sequence counter to max+1.
    op.execute(
        sa.text(
            """
            INSERT INTO agent_events (
                id, created_at, updated_at, agent_run_id, sequence, event_type,
                node_name, tool_name, status, duration_ms, safe_summary,
                agent_step_id, tool_call_id, call_id, payload_json, occurred_at
            )
            SELECT
                gen_random_uuid(),
                COALESCE(s.created_at, now()),
                COALESCE(s.updated_at, now()),
                s.agent_run_id,
                s.step_index,
                CASE WHEN s.status = 'failed' THEN 'node_failed' ELSE 'node_completed' END,
                s.node_name,
                NULL,
                s.status,
                NULL,
                LEFT(COALESCE(s.error_message, s.node_name), 500),
                s.id,
                NULL,
                NULL,
                NULL,
                COALESCE(s.finished_at, s.started_at, s.created_at, now())
            FROM agent_steps s
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO agent_events (
                id, created_at, updated_at, agent_run_id, sequence, event_type,
                node_name, tool_name, status, duration_ms, safe_summary,
                agent_step_id, tool_call_id, call_id, payload_json, occurred_at
            )
            SELECT
                gen_random_uuid(),
                COALESCE(t.created_at, now()),
                COALESCE(t.updated_at, now()),
                t.agent_run_id,
                COALESCE(m.max_seq, -1) + 1 + t.ord::integer,
                CASE WHEN t.status IN ('error', 'failed') THEN 'tool_failed' ELSE 'tool_completed' END,
                t.node_name,
                t.tool_name,
                t.status,
                t.duration_ms,
                LEFT(COALESCE(t.error_message, t.tool_name), 500),
                t.agent_step_id,
                t.id,
                t.call_id,
                NULL,
                COALESCE(t.created_at, now())
            FROM (
                SELECT
                    tc.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY tc.agent_run_id
                        ORDER BY tc.created_at ASC NULLS LAST, tc.id ASC
                    ) - 1 AS ord
                FROM tool_calls tc
            ) t
            LEFT JOIN (
                SELECT agent_run_id, MAX(sequence) AS max_seq
                FROM agent_events
                GROUP BY agent_run_id
            ) m ON m.agent_run_id = t.agent_run_id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE agent_runs r
            SET event_sequence = COALESCE((
                SELECT MAX(e.sequence) + 1 FROM agent_events e WHERE e.agent_run_id = r.id
            ), 0)
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_agent_events_tool_call_id", table_name="agent_events")
    op.drop_index("ix_agent_events_agent_step_id", table_name="agent_events")
    op.drop_index("ix_agent_events_event_type", table_name="agent_events")
    op.drop_index("ix_agent_events_agent_run_id_sequence", table_name="agent_events")
    op.drop_index("ix_agent_events_agent_run_id", table_name="agent_events")
    op.drop_table("agent_events")

    op.drop_index("ix_tool_calls_call_id", table_name="tool_calls")
    op.drop_column("tool_calls", "finished_at")
    op.drop_column("tool_calls", "started_at")
    op.drop_column("tool_calls", "node_name")
    op.drop_column("tool_calls", "call_id")
    op.drop_column("agent_runs", "event_sequence")
