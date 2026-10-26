"""SERVER CHAIN: add requirement block identity: module_document_id, heading_key, title

Same change as the local revision d4a8c1f6b2e9 (without its merge of the two local heads),
chained off the production revision b8f24a6e3d17.

DEPLOYMENT RULE
---------------
    production : copy THIS file into alembic/versions/, then `alembic upgrade head`.
    local      : already covered by d4a8c1f6b2e9 — do not copy this in.

Before deploying, confirm the server's current head with `alembic current`. If the tracker
migrations (a7c3e2f1b904, b5d2c8e4a1f7) were deployed to the server after b8f24a6e3d17, change
down_revision below to whatever `alembic current` reports.

Revision ID: e7b3d9a5c1f0
Revises: b8f24a6e3d17
Create Date: 2026-10-22

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7b3d9a5c1f0'
down_revision: Union[str, Sequence[str], None] = 'b8f24a6e3d17'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE requirements ADD COLUMN IF NOT EXISTS module_document_id UUID")
    op.execute("ALTER TABLE requirements ADD COLUMN IF NOT EXISTS heading_key VARCHAR(500)")
    op.execute("ALTER TABLE requirements ADD COLUMN IF NOT EXISTS title TEXT")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_requirements_module_document_id "
        "ON requirements (module_document_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_requirements_module_heading "
        "ON requirements (module_document_id, heading_key)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_requirements_module_heading")
    op.execute("DROP INDEX IF EXISTS ix_requirements_module_document_id")
    op.execute("ALTER TABLE requirements DROP COLUMN IF EXISTS title")
    op.execute("ALTER TABLE requirements DROP COLUMN IF EXISTS heading_key")
    op.execute("ALTER TABLE requirements DROP COLUMN IF EXISTS module_document_id")
