"""add log_sources + log_alert_rules.log_source_id

Revision ID: h1i2j3k4l5m6
Revises: g1b2c3d4e5f6
Create Date: 2026-09-20 11:00:00.000000

变更（N7 日志源自定义 / 多实例）：
1. 新增 log_sources 表：自定义日志源（name / es_url / index_pattern / username /
   password_encrypted / is_default / enabled / created_by / created_at / updated_at）
2. log_alert_rules 新增 log_source_id（空 = 默认源）

注意：只写迁移文件，不执行 alembic upgrade（沿用 N6 约定）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'h1i2j3k4l5m6'
down_revision: Union[str, None] = 'g1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'log_sources',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('es_url', sa.String(length=500), nullable=False),
        sa.Column('index_pattern', sa.String(length=200), nullable=False, server_default='k8s-*'),
        sa.Column('username', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('password_encrypted', sa.String(length=500), nullable=False, server_default=''),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_by', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_log_sources_is_default', 'log_sources', ['is_default'])
    op.add_column('log_alert_rules', sa.Column('log_source_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('log_alert_rules', 'log_source_id')
    op.drop_index('ix_log_sources_is_default', table_name='log_sources')
    op.drop_table('log_sources')
