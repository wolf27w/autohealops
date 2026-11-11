# -*- coding: utf-8 -*-
"""R10 收尾：prompt_templates 加 gray_pct（auto 模板灰度比例，0~100，默认 100 = 全量）。

Revision ID: m1a2b3c4d5e6
Revises: j7e8f9a0b1c2, l0b1c2d3e4f5
Create Date: 2026-11-11

背景
----
R10 提示词模板收尾需求：auto 来源（source=auto）模板生效时按「集群 id + fault_type」
做稳定 hash 分流，只有 hash%100 < gray_pct 才用该模板，否则回退 parent_id 上一版。
默认 100 = 完全不改变现有行为。

本迁移把此前分叉的两个 head（j7e8f9a0b1c2 / l0b1c2d3e4f5）合并成单一 head。
"""
from alembic import op
import sqlalchemy as sa

revision: str = 'm1a2b3c4d5e6'
down_revision = ("j7e8f9a0b1c2", "l0b1c2d3e4f5")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('prompt_templates', sa.Column('gray_pct', sa.Integer(), nullable=False,
                                                server_default='100'))


def downgrade() -> None:
    op.drop_column('prompt_templates', 'gray_pct')
