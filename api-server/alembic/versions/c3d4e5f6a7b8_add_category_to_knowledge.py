"""add category to knowledge_entries

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-26 16:40:00.000000

变更：
1. knowledge_entries 表加 category 列（分类：pod_crash/db/application/resource/infra/other）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('knowledge_entries', sa.Column('category', sa.String(length=20), nullable=True))
    op.create_index('ix_knowledge_entries_category', 'knowledge_entries', ['category'])


def downgrade() -> None:
    op.drop_index('ix_knowledge_entries_category', table_name='knowledge_entries')
    op.drop_column('knowledge_entries', 'category')
