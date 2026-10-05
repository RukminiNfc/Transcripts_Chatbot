"""Add MOM approval columns, customer ADO target, and mom_action_items

Revision ID: a7c3e2f1b904
Revises: d1f499637722
Create Date: 2026-09-27 12:00:00.000000

Written as raw SQL with IF [NOT] EXISTS because `Base.metadata.create_all` runs at app startup:
depending on whether the app started before this migration, `mom_action_items` may already
exist. `meeting_minutes` itself was only ever created by create_all, never by a migration.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a7c3e2f1b904'
down_revision: Union[str, Sequence[str], None] = 'd1f499637722'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS ado_project VARCHAR(255)")
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS ado_area_path VARCHAR(500)")
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS ado_iteration_path VARCHAR(500)")

    op.execute("ALTER TABLE meeting_minutes ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ")
    op.execute("ALTER TABLE meeting_minutes ADD COLUMN IF NOT EXISTS approved_by VARCHAR(100)")
    op.execute("ALTER TABLE meeting_minutes ADD COLUMN IF NOT EXISTS items_extracted_at TIMESTAMPTZ")

    op.execute("""
        CREATE TABLE IF NOT EXISTS mom_action_items (
            id               UUID PRIMARY KEY,
            mom_id           UUID NOT NULL,
            area             VARCHAR(255),
            title            TEXT NOT NULL,
            description      TEXT,
            owner_name       VARCHAR(255),
            assignee_email   VARCHAR(255),
            due_text         VARCHAR(255),
            due_date         DATE,
            push_status      VARCHAR(20) DEFAULT 'draft',
            push_error       TEXT,
            ado_work_item_id INTEGER,
            ado_url          VARCHAR(500),
            created_at       TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_mom_action_items_mom_id ON mom_action_items (mom_id)")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE IF EXISTS mom_action_items")
    op.execute("ALTER TABLE meeting_minutes DROP COLUMN IF EXISTS items_extracted_at")
    op.execute("ALTER TABLE meeting_minutes DROP COLUMN IF EXISTS approved_by")
    op.execute("ALTER TABLE meeting_minutes DROP COLUMN IF EXISTS approved_at")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS ado_iteration_path")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS ado_area_path")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS ado_project")
