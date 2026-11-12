# -*- coding: utf-8 -*-
"""诊断会话增加「共享到 namespace」字段（R31）

- shared：是否共享到本 namespace（默认 False）
- shared_at：共享时间（取消共享时置空）
- shared_by：操作共享的用户 id（取消共享时置空）

Revision ID: a9b8c7d6e5f4
Revises: o1c2d3e4f5a6
Create Date: 2026-11-12
"""
from alembic import op
import sqlalchemy as sa

revision: str = 'a9b8c7d6e5f4'
down_revision = 'o1c2d3e4f5a6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('diagnosis_sessions', sa.Column('shared', sa.Boolean(), nullable=False, server_default=sa.text('0')))
    op.add_column('diagnosis_sessions', sa.Column('shared_at', sa.DateTime(), nullable=True))
    op.add_column('diagnosis_sessions', sa.Column('shared_by', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('diagnosis_sessions', 'shared_by')
    op.drop_column('diagnosis_sessions', 'shared_at')
    op.drop_column('diagnosis_sessions', 'shared')
