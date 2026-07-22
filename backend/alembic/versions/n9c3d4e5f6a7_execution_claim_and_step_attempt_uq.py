"""Persist execution claim fields and unique (run, node, attempt) on agent_steps.

Revision ID: n9c3d4e5f6a7
Revises: m8b2c3d4e5f6
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "n9c3d4e5f6a7"
down_revision: str | None = "m8b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("execution_claim_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("execution_action", sa.String(length=32), nullable=True),
    )

    # Deterministic attempt backfill before unique constraint.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
              SELECT id,
                     ROW_NUMBER() OVER (
                       PARTITION BY agent_run_id, node_name
                       ORDER BY step_index ASC, created_at ASC, id ASC
                     ) AS rn
              FROM agent_steps
            )
            UPDATE agent_steps AS s
            SET attempt = ranked.rn
            FROM ranked
            WHERE s.id = ranked.id
            """
        )
    )
    op.create_unique_constraint(
        "uq_agent_steps_agent_run_id_node_name_attempt",
        "agent_steps",
        ["agent_run_id", "node_name", "attempt"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_agent_steps_agent_run_id_node_name_attempt",
        "agent_steps",
        type_="unique",
    )
    op.drop_column("agent_runs", "execution_action")
    op.drop_column("agent_runs", "execution_claim_token")
