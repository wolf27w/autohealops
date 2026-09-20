"""add knowledge is_active (治理：失效停用)

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-09-12 11:55:00.000000

变更（C3·知识库治理）：knowledge_entries 加 is_active（失效停用后不参与检索）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'd0e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('knowledge_entries', sa.Column('is_active', sa.Boolean(), nullable=True, server_default=sa.text('1')))
    op.create_index('ix_knowledge_is_active', 'knowledge_entries', ['is_active'])


def downgrade() -> None:
    op.drop_index('ix_knowledge_is_active', table_name='knowledge_entries')
    op.drop_column('knowledge_entries', 'is_active')
