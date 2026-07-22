"""Add attempt column to agent_steps for node retry lifecycle.

Revision ID: m8b2c3d4e5f6
Revises: l7a1b2c3d4e5
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "m8b2c3d4e5f6"
down_revision: str | None = "l7a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_steps",
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=True),
    )
    # Deterministic backfill for historical rows.
    op.execute(sa.text("UPDATE agent_steps SET attempt = 1 WHERE attempt IS NULL"))
    op.alter_column(
        "agent_steps",
        "attempt",
        existing_type=sa.Integer(),
        nullable=False,
        server_default="1",
    )


def downgrade() -> None:
    op.drop_column("agent_steps", "attempt")
