"""add cluster monitoring fields (Netdata)

Revision ID: x7y8z9a0b1c2
Revises: w6x7y8z9a0b1
Create Date: 2026-10-13 12:10:00.000000

集群监控（Netdata）：开关 + 状态 + 反代路径 + 参数 + stream key 密文。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'x7y8z9a0b1c2'
down_revision: Union[str, None] = 'w6x7y8z9a0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('clusters', sa.Column('monitoring_enabled', sa.Boolean(), nullable=False, server_default=sa.text('0')))
    op.add_column('clusters', sa.Column('monitoring_status', sa.String(length=255), nullable=False, server_default='none'))
    op.add_column('clusters', sa.Column('monitoring_url', sa.String(length=255), nullable=False, server_default=''))
    op.add_column('clusters', sa.Column('monitoring_ns', sa.String(length=64), nullable=False, server_default='apiops-monitoring'))
    op.add_column('clusters', sa.Column('parent_storage_size', sa.String(length=16), nullable=False, server_default='20Gi'))
    op.add_column('clusters', sa.Column('monitoring_retention_days', sa.Integer(), nullable=False, server_default=sa.text('7')))
    op.add_column('clusters', sa.Column('stream_key_encrypted', sa.String(length=255), nullable=False, server_default=''))


def downgrade() -> None:
    for name in ('stream_key_encrypted', 'monitoring_retention_days', 'parent_storage_size',
                 'monitoring_ns', 'monitoring_url', 'monitoring_status', 'monitoring_enabled'):
        op.drop_column('clusters', name)
