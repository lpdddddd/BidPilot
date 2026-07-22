"""Add UNIQUE(agent_run_id, step_index) on agent_steps.

Revision ID: j5e9f0a1b2c3
Revises: i4d8e9f0a1b2
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "j5e9f0a1b2c3"
down_revision: str | None = "i4d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Deduplicate any legacy collisions before adding the unique constraint.
    op.execute(
        sa.text(
            """
            DELETE FROM agent_steps a
            USING agent_steps b
            WHERE a.agent_run_id = b.agent_run_id
              AND a.step_index = b.step_index
              AND a.id < b.id
            """
        )
    )
    op.drop_index(
        "ix_agent_steps_agent_run_id_step_index",
        table_name="agent_steps",
    )
    op.create_index(
        "uq_agent_steps_agent_run_id_step_index",
        "agent_steps",
        ["agent_run_id", "step_index"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_agent_steps_agent_run_id_step_index",
        table_name="agent_steps",
    )
    op.create_index(
        "ix_agent_steps_agent_run_id_step_index",
        "agent_steps",
        ["agent_run_id", "step_index"],
        unique=False,
    )
