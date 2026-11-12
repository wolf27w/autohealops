# -*- coding: utf-8 -*-
"""新增资源治理·管控表（R1，工作负载级资源管控 + 批量扩缩容）

Revision ID: bfe08be41a5b
Revises: a9b8c7d6e5f4
Create Date: 2026-11-12

字段口径：
- demand_replicas / limit_replicas：需求副本数 / 限制副本数（-1=不管控/不限制）
- cpu_request / cpu_limit / memory_request / memory_limit：需求值(request) / 限制值(limit)
- jvm_xms / jvm_xmx：JVM 启动参数
- new_service_exempt：新服务免确认
- exempt_start / exempt_end：临时扩容豁免窗口（窗口内允许突破 limit_replicas）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'bfe08be41a5b'
down_revision: Union[str, None] = 'a9b8c7d6e5f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'resource_governance',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('cluster_id', sa.Integer(), nullable=False),
        sa.Column('namespace', sa.String(length=120), nullable=False),
        sa.Column('workload_kind', sa.String(length=20), nullable=False, server_default='deployment'),
        sa.Column('workload_name', sa.String(length=200), nullable=False),
        sa.Column('demand_replicas', sa.Integer(), nullable=False, server_default='-1'),
        sa.Column('limit_replicas', sa.Integer(), nullable=False, server_default='-1'),
        sa.Column('cpu_request', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('cpu_limit', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('memory_request', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('memory_limit', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('jvm_xms', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('jvm_xmx', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('new_service_exempt', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('exempt_start', sa.DateTime(), nullable=True),
        sa.Column('exempt_end', sa.DateTime(), nullable=True),
        sa.Column('remark', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('cluster_id', 'namespace', 'workload_kind', 'workload_name', name='uq_rg_workload_scope'),
    )
    op.create_index('ix_resource_governance_cluster_id', 'resource_governance', ['cluster_id'])
    op.create_index('ix_resource_governance_namespace', 'resource_governance', ['namespace'])
    op.create_index('ix_resource_governance_workload_name', 'resource_governance', ['workload_name'])


def downgrade() -> None:
    op.drop_index('ix_resource_governance_workload_name', table_name='resource_governance')
    op.drop_index('ix_resource_governance_namespace', table_name='resource_governance')
    op.drop_index('ix_resource_governance_cluster_id', table_name='resource_governance')
    op.drop_table('resource_governance')
