"""add knowledge execution_steps

Revision ID: 9a1c2b3d4e5f
Revises: 3eb88895be6f
Create Date: 2026-08-20 12:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a1c2b3d4e5f'
down_revision: Union[str, None] = '3eb88895be6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 知识库加可执行修复步骤字段（KB 命中零 AI 用）
    op.add_column('knowledge_entries', sa.Column('execution_steps', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('knowledge_entries', 'execution_steps')
