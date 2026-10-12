"""ai_model_configs 增加 is_default（默认模型标记）

Revision ID: s2t3u4v5w6x7
Revises: r1s2t3u4v5w6
Create Date: 2026-09-23 14:35:00

默认模型：全局唯一，功能调用优先使用；不可用时降级到 priority 链其他模型。
"""
from alembic import op
import sqlalchemy as sa

revision: str = 's2t3u4v5w6x7'
down_revision: str = 'r1s2t3u4v5w6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('ai_model_configs', sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('ai_model_configs', 'is_default')
