"""add self_heal_rules

Revision ID: a1b2c3d4e5f6
Revises: e4f5a6b7c8d9
Create Date: 2026-08-26 15:00:00.000000

变更：
1. 新表 self_heal_rules（自愈场景规则：强制人工/强制自动覆盖，含影子模式）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'e4f5a6b7c8d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'self_heal_rules',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('cluster_id', sa.Integer(), nullable=True, index=True),
        sa.Column('namespace_match', sa.String(length=100), default=''),
        sa.Column('fault_type_match', sa.String(length=100), default=''),
        sa.Column('resource_type_match', sa.String(length=50), default=''),
        sa.Column('action', sa.String(length=20), nullable=False),
        sa.Column('priority', sa.Integer(), default=100),
        sa.Column('shadow', sa.Boolean(), default=True),
        sa.Column('enabled', sa.Boolean(), default=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('self_heal_rules')
