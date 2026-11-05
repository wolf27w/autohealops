# -*- coding: utf-8 -*-
"""pod_metrics 5 分钟聚合表（指标分层存储）

背景（老板 2026-11-05）：原始 60s 采样保留 7 天行数太大（每 Pod 每天 1440 行）。
改为分层：原始 60s 只保 1 天；按 5 分钟聚合保 7 天（10分钟/1小时/24小时档位读原始，3天/7天读聚合）。

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, None] = 'e6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pod_metrics_5m',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('cluster_id', sa.Integer(), nullable=False),
        sa.Column('namespace', sa.String(100), server_default='', nullable=False),
        sa.Column('pod_name', sa.String(200), server_default='', nullable=False),
        sa.Column('bucket_at', sa.DateTime(), nullable=False),
        sa.Column('cpu_avg', sa.Float(), server_default='0', nullable=False),
        sa.Column('mem_avg', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('net_rx_max', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('net_tx_max', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('samples', sa.Integer(), server_default='0', nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cluster_id', 'namespace', 'pod_name', 'bucket_at', name='uk_pm5m'),
        mysql_charset='utf8mb4',
    )
    op.create_index('ix_pm5m_bucket', 'pod_metrics_5m', ['bucket_at'])
    op.create_index('ix_pm5m_pod', 'pod_metrics_5m', ['cluster_id', 'namespace', 'pod_name'])


def downgrade() -> None:
    op.drop_index('ix_pm5m_pod', table_name='pod_metrics_5m')
    op.drop_index('ix_pm5m_bucket', table_name='pod_metrics_5m')
    op.drop_table('pod_metrics_5m')
