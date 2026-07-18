"""Add ocr_required value to parse_status enum

Revision ID: b7c1d2e3f4a5
Revises: a34e7a76f341
Create Date: 2026-07-19
"""

from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c1d2e3f4a5"
down_revision: Union[str, None] = "a34e7a76f341"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block on older
    # PostgreSQL; use autocommit to stay compatible.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE parse_status ADD VALUE IF NOT EXISTS 'ocr_required'")


def downgrade() -> None:
    # PostgreSQL cannot remove enum values; rows using ocr_required would need
    # manual migration. Intentionally a no-op.
    pass
