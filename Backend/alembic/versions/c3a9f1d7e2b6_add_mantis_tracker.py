"""Mantis as a third tracker

Revision ID: c3a9f1d7e2b6
Revises: b5d2c8e4a1f7
Create Date: 2026-10-22 12:00:00.000000

- customers.mantis_project / mantis_category: the per-customer Mantis target (tracker = 'mantis').
  Assignees need no column: the Mantis username is the assignee email's prefix.

Raw SQL with IF [NOT] EXISTS for the same reason as a7c3e2f1b904: create_all at startup may have
already added the new columns.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c3a9f1d7e2b6'
down_revision: Union[str, Sequence[str], None] = 'b5d2c8e4a1f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS mantis_project VARCHAR(255)")
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS mantis_category VARCHAR(255)")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE customers SET tracker = NULL WHERE tracker = 'mantis'")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS mantis_category")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS mantis_project")
