# -*- coding: utf-8 -*-
"""删除资源治理管控表（准入 webhook 已砍，重复造轮子）

Revision ID: a1b2c3d4e5f6
Revises: z9a0b1c2d3e4
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, None] = 'z9a0b1c2d3e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('resource_governance')


def downgrade() -> None:
    # 回退无法恢复原表结构，仅留空（原表由旧迁移创建，不再需要）
    pass
