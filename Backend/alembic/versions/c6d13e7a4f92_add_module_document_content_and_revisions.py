"""add module document content and revision history

module_documents.content_markdown holds the current specification; module_document_revisions
holds every past version with the model's own account of what changed.

LOCAL CHAIN. Production equivalent is in alembic/server_versions/.

Revision ID: c6d13e7a4f92
Revises: f2a95c8e1b63
Create Date: 2026-10-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c6d13e7a4f92'
down_revision: Union[str, Sequence[str], None] = 'f2a95c8e1b63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('module_documents', sa.Column('content_markdown', sa.Text(), nullable=True))

    op.create_table(
        'module_document_revisions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('module_document_id', postgresql.UUID(as_uuid=True), nullable=False),
        # Nullable: a human edit is a revision with no meeting behind it.
        sa.Column('transcript_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('session_name', sa.String(length=255), nullable=True),
        sa.Column('version_label', sa.String(length=50), nullable=False),
        sa.Column('revision_summary', sa.Text(), nullable=True),
        sa.Column('line_diffs', sa.JSON(), nullable=True),
        # The whole document at this version, so viewing v1.2 is a read rather than a replay
        # of every diff since v1.0.
        sa.Column('content_snapshot', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_module_document_revisions_module_document_id'),
                    'module_document_revisions', ['module_document_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_module_document_revisions_module_document_id'),
                  table_name='module_document_revisions')
    op.drop_table('module_document_revisions')
    op.drop_column('module_documents', 'content_markdown')
