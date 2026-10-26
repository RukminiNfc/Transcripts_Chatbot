"""add requirement block identity: module_document_id, heading_key, title

A requirement built from a module document is one MOM sub-section ("Topic: Sub-section" plus its
bullets), not one bullet. These columns give it an identity that survives re-indexing:

    module_document_id   which module document the requirement belongs to. Until now the only
                         link was the module NAME in `category`, which a rename would break.
    heading_key          the block's title, normalized. With module_document_id it is how the
                         same requirement is recognised in the next meeting, so it keeps its id
                         and gains a version instead of being deleted and re-created.
    title                the block's title as written, for display.

The pair (module_document_id, heading_key) is unique. Rows from the old pipeline leave both NULL,
and Postgres treats NULLs as distinct, so they are unaffected by the constraint.

Also MERGES the two local heads (b5d2c8e4a1f7 tracker chain, c6d13e7a4f92 module documents), so
`alembic upgrade head` works again instead of needing `upgrade heads`.

LOCAL CHAIN. Production equivalent is alembic/server_versions/e7b3d9a5c1f0.

Revision ID: d4a8c1f6b2e9
Revises: b5d2c8e4a1f7, c6d13e7a4f92
Create Date: 2026-10-22

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4a8c1f6b2e9'
down_revision: Union[str, Sequence[str], None] = ('b5d2c8e4a1f7', 'c6d13e7a4f92')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF NOT EXISTS throughout so the revision is safe to re-run on a database where init_db's
    # create_all got there first.
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
