"""Add attempt + idempotency_key for real tool lifecycle events.

Revision ID: l7a1b2c3d4e5
Revises: k6f0a1b2c3d4
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "l7a1b2c3d4e5"
down_revision: str | None = "k6f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tool_calls",
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column("agent_events", sa.Column("attempt", sa.Integer(), nullable=True))
    op.add_column(
        "agent_events",
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "uq_agent_events_agent_run_id_idempotency_key",
        "agent_events",
        ["agent_run_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_agent_events_agent_run_id_idempotency_key",
        table_name="agent_events",
    )
    op.drop_column("agent_events", "idempotency_key")
    op.drop_column("agent_events", "attempt")
    op.drop_column("tool_calls", "attempt")
