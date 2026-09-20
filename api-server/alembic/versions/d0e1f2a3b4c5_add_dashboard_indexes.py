"""add indexes for dashboard aggregation

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-09-12 11:30:00.000000

变更（性能）：仪表盘聚合涉及大表扫描，补复合索引
- resource_snapshots(cluster_id, resource_type, snapshot_time)：按集群+类型取最新快照
- pod_metrics(cluster_id, sampled_at)：按集群取最新采样
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_rs_cluster_type_time', 'resource_snapshots',
        ['cluster_id', 'resource_type', 'snapshot_time'], unique=False,
    )
    op.create_index(
        'ix_pm_cluster_sampled', 'pod_metrics',
        ['cluster_id', 'sampled_at'], unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_pm_cluster_sampled', table_name='pod_metrics')
    op.drop_index('ix_rs_cluster_type_time', table_name='resource_snapshots')
