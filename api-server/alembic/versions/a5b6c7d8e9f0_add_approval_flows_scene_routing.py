"""add approval flows + notification scene routing + project notify config

Revision ID: a5b6c7d8e9f0
Revises: f0a1b2c3d4e5
Create Date: 2026-08-25 13:40:00.000000

变更：
1. 新表 approval_flows（自定义审批流程）+ 内置默认流程
2. notification_configs 加 scene（alert/approval_duty/approval_admin/both）+ namespaces（告警 namespace 匹配）
3. projects 加 notify_config_id（项目组群通知配置）
4. approvals 加 flow_id/flow_name/flow_nodes/current_node_index（流程快照）
5. project_cluster_ns 加 (cluster_id, namespace) 唯一索引（一个 namespace 唯一归属一个项目）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a5b6c7d8e9f0'
down_revision: Union[str, None] = 'f0a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_FLOW_NODES = (
    '[{"type": "duty", "timeout": 20, "approve_count": 0, "on_empty": "next", "group": ""},'
    '{"type": "group_all", "timeout": 30, "approve_count": 0, "group": "duty", "include_admin": true},'
    '{"type": "admin", "timeout": 60, "approve_count": 0}]'
)


def upgrade() -> None:
    # 1. 审批流程表 + 内置默认流程（不可删除）
    op.create_table(
        'approval_flows',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.String(length=500), default=''),
        sa.Column('nodes', sa.Text(), default='[]'),
        sa.Column('is_default', sa.Boolean(), default=False),
        sa.Column('is_active', sa.Boolean(), default=True, index=True),
        sa.Column('created_by', sa.Integer(), default=0),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.execute(
        sa.text(
            "INSERT INTO approval_flows (name, description, nodes, is_default, is_active, created_by) "
            "VALUES ('默认流程（值班人 → @群所有人+admin）', "
            "'内置默认流程：当天值班人审批，无值班人或超时 → @值班群所有人+admin，admin 也超时 → 审批超时关闭。不可删除。', "
            ":nodes, 1, 1, 0)"
        ).bindparams(nodes=DEFAULT_FLOW_NODES)
    )

    # 2. 通知配置：用途路由 + namespace 匹配
    op.add_column('notification_configs', sa.Column('scene', sa.String(length=20), server_default='both', nullable=False))
    op.add_column('notification_configs', sa.Column('namespaces', sa.Text(), default='', nullable=True))
    op.create_index('ix_notification_configs_scene', 'notification_configs', ['scene'])

    # 3. 项目：项目组群通知配置
    op.add_column('projects', sa.Column('notify_config_id', sa.Integer(), nullable=True))
    op.create_index('ix_projects_notify_config_id', 'projects', ['notify_config_id'])

    # 4. 审批单：流程引用 + 节点快照
    op.add_column('approvals', sa.Column('flow_id', sa.Integer(), nullable=True))
    op.add_column('approvals', sa.Column('flow_name', sa.String(length=100), default='', nullable=True))
    op.add_column('approvals', sa.Column('flow_nodes', sa.Text(), default='', nullable=True))
    op.add_column('approvals', sa.Column('current_node_index', sa.Integer(), default=0, nullable=True))
    op.create_index('ix_approvals_flow_id', 'approvals', ['flow_id'])

    # 5. namespace 唯一归属一个项目
    op.create_unique_constraint(
        'uq_project_cluster_ns_cluster_namespace',
        'project_cluster_ns',
        ['cluster_id', 'namespace'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_project_cluster_ns_cluster_namespace', 'project_cluster_ns', type_='unique')
    op.drop_index('ix_approvals_flow_id', table_name='approvals')
    op.drop_column('approvals', 'current_node_index')
    op.drop_column('approvals', 'flow_nodes')
    op.drop_column('approvals', 'flow_name')
    op.drop_column('approvals', 'flow_id')
    op.drop_index('ix_projects_notify_config_id', table_name='projects')
    op.drop_column('projects', 'notify_config_id')
    op.drop_index('ix_notification_configs_scene', table_name='notification_configs')
    op.drop_column('notification_configs', 'namespaces')
    op.drop_column('notification_configs', 'scene')
    op.drop_table('approval_flows')
