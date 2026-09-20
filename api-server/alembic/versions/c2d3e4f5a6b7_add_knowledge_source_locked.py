"""add knowledge source/is_locked

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-08-25 16:10:00.000000

变更：
1. knowledge_entries 加 source（ai/manual）+ is_locked（AI 沉淀默认锁定，编辑需 admin 解锁）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c2d3e4f5a6b7'
down_revision: Union[str, None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('knowledge_entries', sa.Column('source', sa.String(length=10), server_default='manual', nullable=False))
    op.add_column('knowledge_entries', sa.Column('is_locked', sa.Boolean(), server_default=sa.text('0'), nullable=False))
    op.create_index('ix_knowledge_entries_source', 'knowledge_entries', ['source'])


def downgrade() -> None:
    op.drop_index('ix_knowledge_entries_source', table_name='knowledge_entries')
    op.drop_column('knowledge_entries', 'is_locked')
    op.drop_column('knowledge_entries', 'source')
