"""add original_markdown and edited_at to meeting_minutes

Lets an admin correct a draft before sending (mis-transcribed words, mostly) while keeping what
the model originally wrote. Edits overwrite content_markdown in place — they are corrections,
not new versions — so without original_markdown the model's own output would be lost on every
fix, and MOM quality would become unmeasurable.

LOCAL CHAIN. The production equivalent lives in alembic/server_versions/ — see the README there.

Revision ID: d8e42b1c9f05
Revises: b4f82a6c1e37
Create Date: 2026-10-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd8e42b1c9f05'
down_revision: Union[str, Sequence[str], None] = 'b4f82a6c1e37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('meeting_minutes', sa.Column('original_markdown', sa.Text(), nullable=True))
    op.add_column('meeting_minutes',
                  sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('meeting_minutes', 'edited_at')
    op.drop_column('meeting_minutes', 'original_markdown')
