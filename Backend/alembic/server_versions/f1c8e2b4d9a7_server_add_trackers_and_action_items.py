"""SERVER CHAIN: add trackers (ADO, Jira, Mantis), MOM approval fields, and mom_action_items

Applies tracker choice and action items to the server chain, chained directly off
the production revision e7b3d9a5c1f0.

Revision ID: f1c8e2b4d9a7
Revises: e7b3d9a5c1f0
Create Date: 2026-10-28

"""
from typing import Sequence, Union
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f1c8e2b4d9a7'
down_revision: Union[str, Sequence[str], None] = 'e7b3d9a5c1f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Customer tracker fields
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS tracker VARCHAR(20)")
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS ado_project VARCHAR(255)")
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS ado_area_path VARCHAR(500)")
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS ado_iteration_path VARCHAR(500)")
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS jira_project_key VARCHAR(50)")
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS mantis_project VARCHAR(255)")
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS mantis_category VARCHAR(255)")

    # 2. Meeting Minutes approval fields
    op.execute("ALTER TABLE meeting_minutes ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ")
    op.execute("ALTER TABLE meeting_minutes ADD COLUMN IF NOT EXISTS approved_by VARCHAR(100)")
    op.execute("ALTER TABLE meeting_minutes ADD COLUMN IF NOT EXISTS items_extracted_at TIMESTAMPTZ")

    # 3. Action Items table
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
            tracker          VARCHAR(20),
            external_key     VARCHAR(50),
            external_url     VARCHAR(500),
            created_at       TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_mom_action_items_mom_id ON mom_action_items (mom_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mom_action_items")
    op.execute("ALTER TABLE meeting_minutes DROP COLUMN IF EXISTS items_extracted_at")
    op.execute("ALTER TABLE meeting_minutes DROP COLUMN IF EXISTS approved_by")
    op.execute("ALTER TABLE meeting_minutes DROP COLUMN IF EXISTS approved_at")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS mantis_category")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS mantis_project")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS jira_project_key")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS ado_iteration_path")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS ado_area_path")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS ado_project")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS tracker")
