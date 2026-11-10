# -*- coding: utf-8 -*-
"""R13：未分类故障沉淀提示词——prompt_templates 加 status/source/source_refs/parent_id/invalid_count + 新表 prompt_suggestions

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('prompt_templates', sa.Column('status', sa.String(length=20), nullable=False,
                                                server_default='active'))
    op.add_column('prompt_templates', sa.Column('source', sa.String(length=20), nullable=False,
                                                server_default='manual'))
    op.add_column('prompt_templates', sa.Column('source_refs', sa.Text(), nullable=True))
    op.add_column('prompt_templates', sa.Column('parent_id', sa.Integer(), nullable=True))
    op.add_column('prompt_templates', sa.Column('invalid_count', sa.Integer(), nullable=False,
                                                server_default='0'))

    op.create_table(
        'prompt_suggestions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('fault_type', sa.String(length=50), nullable=False, index=True),
        sa.Column('feature', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('hit_count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('sample_logs', sa.Text(), nullable=True),
        sa.Column('root_cause', sa.Text(), nullable=True),
        sa.Column('suggestion_name', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('system_prompt', sa.Text(), nullable=True),
        sa.Column('user_prompt_template', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('similarity_note', sa.String(length=300), nullable=False, server_default=''),
        sa.Column('source_refs', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.String(length=50), nullable=False, server_default='system'),
    )
    op.create_index('ix_prompt_suggestions_status', 'prompt_suggestions', ['status'])


def downgrade() -> None:
    op.drop_index('ix_prompt_suggestions_status', table_name='prompt_suggestions')
    op.drop_table('prompt_suggestions')
    op.drop_column('prompt_templates', 'invalid_count')
    op.drop_column('prompt_templates', 'parent_id')
    op.drop_column('prompt_templates', 'source_refs')
    op.drop_column('prompt_templates', 'source')
    op.drop_column('prompt_templates', 'status')
