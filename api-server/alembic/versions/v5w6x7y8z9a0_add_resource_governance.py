"""add resource_governance table (P0-R1)

Revision ID: v5w6x7y8z9a0
Revises: u4v5w6x7y8z9
Create Date: 2026-10-12 21:00:00.000000

新增「资源治理·管控表」：按 集群+命名空间+工作负载 存期望副本数/资源配额/JVM 参数，
供准入控制（MutatingWebhook）在 Deployment 创建/发布时改写 spec 使用。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'v5w6x7y8z9a0'
down_revision: Union[str, None] = 'u4v5w6x7y8z9'
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
        sa.Column('container', sa.String(length=120), nullable=False, server_default=''),
        sa.Column('replicas', sa.Integer(), nullable=False, server_default='-1'),
        sa.Column('cpu_request', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('cpu_limit', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('memory_request', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('memory_limit', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('jvm_xms', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('jvm_xmx', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('jvm_xss', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('jvm_max_metaspace', sa.String(length=20), nullable=False, server_default=''),
        sa.Column('admission_enabled', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('remark', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint('cluster_id', 'namespace', 'workload_kind', 'workload_name', 'container', name='uq_rg_scope'),
    )
    op.create_index('ix_resource_governance_cluster_id', 'resource_governance', ['cluster_id'])
    op.create_index('ix_resource_governance_namespace', 'resource_governance', ['namespace'])
    op.create_index('ix_resource_governance_workload_name', 'resource_governance', ['workload_name'])


def downgrade() -> None:
    op.drop_index('ix_resource_governance_workload_name', table_name='resource_governance')
    op.drop_index('ix_resource_governance_namespace', table_name='resource_governance')
    op.drop_index('ix_resource_governance_cluster_id', table_name='resource_governance')
    op.drop_table('resource_governance')
