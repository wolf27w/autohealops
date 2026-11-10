# -*- coding: utf-8 -*-
"""R10 提示词场景体系化：公共片段 / 场景字段 / 路由表

1. `prompt_fragments`：公共片段（输出格式、红线、术语…），模板用 `{{fragment:key}}` 引用
2. `prompt_templates` 加 `scene`（pod/node/workload/database/cluster/action）与 `sample_vars`（预览用样例变量 JSON）
3. `prompt_routing`：(match_type, match_value, match_mode) → template_name，带优先级；替代硬编码/R11 JSON

Revision ID: k8f9a0b1c2d3
Revises: i6d7e8f9a0b1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'k8f9a0b1c2d3'
down_revision: Union[str, None] = 'i6d7e8f9a0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'prompt_fragments',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('key', sa.String(length=60), nullable=False, unique=True),
        sa.Column('name', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('category', sa.String(length=30), nullable=False, server_default='common'),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.add_column('prompt_templates', sa.Column('scene', sa.String(length=30), nullable=False, server_default=''))
    op.add_column('prompt_templates', sa.Column('sample_vars', sa.Text(), nullable=True))
    op.create_table(
        'prompt_routing',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('match_type', sa.String(length=20), nullable=False, server_default='fault_type'),
        sa.Column('match_value', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('match_mode', sa.String(length=10), nullable=False, server_default='exact'),
        sa.Column('template_name', sa.String(length=100), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_prompt_routing_lookup', 'prompt_routing', ['match_type', 'match_mode', 'match_value'])


def downgrade() -> None:
    op.drop_index('ix_prompt_routing_lookup', table_name='prompt_routing')
    op.drop_table('prompt_routing')
    op.drop_column('prompt_templates', 'sample_vars')
    op.drop_column('prompt_templates', 'scene')
    op.drop_table('prompt_fragments')
