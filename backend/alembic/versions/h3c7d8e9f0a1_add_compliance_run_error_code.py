"""Add error_code to compliance_runs for durable failed-run persistence.

Revision ID: h3c7d8e9f0a1
Revises: g2b6c7d8e9f0
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "h3c7d8e9f0a1"
down_revision: str | None = "g2b6c7d8e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "compliance_runs",
        sa.Column("error_code", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("compliance_runs", "error_code")
