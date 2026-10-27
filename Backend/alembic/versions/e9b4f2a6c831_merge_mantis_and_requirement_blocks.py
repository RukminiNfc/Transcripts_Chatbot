"""merge heads: Mantis tracker (c3a9f1d7e2b6) and requirement block identity (d4a8c1f6b2e9)

Both branched from b5d2c8e4a1f7 on separate feature branches. No schema change; this only joins
the two heads so `alembic upgrade head` works again instead of needing `upgrade heads`.

Revision ID: e9b4f2a6c831
Revises: c3a9f1d7e2b6, d4a8c1f6b2e9
Create Date: 2026-10-26

"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = 'e9b4f2a6c831'
down_revision: Union[str, Sequence[str], None] = ('c3a9f1d7e2b6', 'd4a8c1f6b2e9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
