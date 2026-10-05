"""Per-customer tracker choice (Azure Boards | Jira) and tracker-neutral action-item keys

Revision ID: b5d2c8e4a1f7
Revises: a7c3e2f1b904
Create Date: 2026-10-05 12:00:00.000000

- customers.tracker ('ado' | 'jira' | NULL) + customers.jira_project_key.
  Customers that already have an ADO project + area path are set to tracker = 'ado', so nothing
  that worked before stops working.
- mom_action_items: ado_work_item_id / ado_url are replaced by tracker / external_key /
  external_url (a Jira key like "CAL-123" is not an integer). Existing values are copied across
  before the old columns are dropped.

Raw SQL with IF [NOT] EXISTS for the same reason as a7c3e2f1b904: create_all at startup may have
already added the new columns.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b5d2c8e4a1f7'
down_revision: Union[str, Sequence[str], None] = 'a7c3e2f1b904'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS tracker VARCHAR(20)")
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS jira_project_key VARCHAR(50)")
    op.execute("""
        UPDATE customers SET tracker = 'ado'
        WHERE tracker IS NULL AND ado_project IS NOT NULL AND ado_area_path IS NOT NULL
    """)

    op.execute("ALTER TABLE mom_action_items ADD COLUMN IF NOT EXISTS tracker VARCHAR(20)")
    op.execute("ALTER TABLE mom_action_items ADD COLUMN IF NOT EXISTS external_key VARCHAR(50)")
    op.execute("ALTER TABLE mom_action_items ADD COLUMN IF NOT EXISTS external_url VARCHAR(500)")
    # Copy only if the old columns are still there (keeps the migration re-runnable).
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'mom_action_items' AND column_name = 'ado_work_item_id') THEN
                UPDATE mom_action_items
                SET tracker = 'ado', external_key = ado_work_item_id::text, external_url = ado_url
                WHERE ado_work_item_id IS NOT NULL AND external_key IS NULL;
            END IF;
        END $$;
    """)
    op.execute("ALTER TABLE mom_action_items DROP COLUMN IF EXISTS ado_work_item_id")
    op.execute("ALTER TABLE mom_action_items DROP COLUMN IF EXISTS ado_url")


def downgrade() -> None:
    """Downgrade schema. Jira-created keys cannot be represented in the old integer column and are dropped."""
    op.execute("ALTER TABLE mom_action_items ADD COLUMN IF NOT EXISTS ado_work_item_id INTEGER")
    op.execute("ALTER TABLE mom_action_items ADD COLUMN IF NOT EXISTS ado_url VARCHAR(500)")
    op.execute("""
        UPDATE mom_action_items
        SET ado_work_item_id = external_key::integer, ado_url = external_url
        WHERE tracker = 'ado' AND external_key ~ '^[0-9]+$'
    """)
    op.execute("ALTER TABLE mom_action_items DROP COLUMN IF EXISTS external_url")
    op.execute("ALTER TABLE mom_action_items DROP COLUMN IF EXISTS external_key")
    op.execute("ALTER TABLE mom_action_items DROP COLUMN IF EXISTS tracker")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS jira_project_key")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS tracker")
