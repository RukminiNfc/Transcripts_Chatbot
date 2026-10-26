"""SERVER CHAIN: add module_documents — the per-customer module registry

Module names come from this table instead of being invented per transcript. Parsing four real
MOMs produced 117 section headings, all unique, which would have become 117 documents rather
than the ~14 product areas they actually describe.

Same change as the local revision f2a95c8e1b63, chained off the production revision.
See alembic/server_versions/README.md.

DEPLOYMENT RULE
---------------
    production : copy THIS file into alembic/versions/, then `alembic upgrade head`.
    local      : already covered by f2a95c8e1b63 — do not copy this in.

Revision ID: a3b71d5f9c24
Revises: e1a73c6d4820
Create Date: 2026-10-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a3b71d5f9c24'
down_revision: Union[str, Sequence[str], None] = 'e1a73c6d4820'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'module_documents',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('customer_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('module_name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=True, server_default='active'),
        sa.Column('gdoc_file_id', sa.String(length=255), nullable=True),
        sa.Column('gdoc_url', sa.Text(), nullable=True),
        sa.Column('current_version', sa.String(length=50), nullable=True, server_default='v1.0'),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        # One name per customer. Two customers may hold the same name independently — these
        # describe one product, and a retail client's modules differ entirely from a staffing one's.
        sa.UniqueConstraint('customer_id', 'module_name', name='uq_customer_module'),
    )
    op.create_index(op.f('ix_module_documents_customer_id'),
                    'module_documents', ['customer_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_module_documents_customer_id'), table_name='module_documents')
    op.drop_table('module_documents')
