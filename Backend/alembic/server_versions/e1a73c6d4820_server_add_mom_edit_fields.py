"""SERVER CHAIN: add original_markdown and edited_at to meeting_minutes

Same change as the local revision d8e42b1c9f05, chained off the production revision instead.
See alembic/server_versions/README.md for why the two chains exist.

DEPLOYMENT RULE
---------------
    production : copy THIS file into alembic/versions/, then `alembic upgrade head`.
    local      : already covered by d8e42b1c9f05 — do not copy this in.

Revision ID: e1a73c6d4820
Revises: c5d91f3a7b22
Create Date: 2026-10-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e1a73c6d4820'
down_revision: Union[str, Sequence[str], None] = 'c5d91f3a7b22'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('meeting_minutes', sa.Column('original_markdown', sa.Text(), nullable=True))
    op.add_column('meeting_minutes',
                  sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('meeting_minutes', 'edited_at')
    op.drop_column('meeting_minutes', 'original_markdown')
