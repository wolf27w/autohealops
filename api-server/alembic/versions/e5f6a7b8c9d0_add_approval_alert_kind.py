"""add approval alert_id & kind, plan_id nullable

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-10 18:20:00.000000

变更（批一 · 审核闭环）：
1. approvals.plan_id 改可空（人工协助单无 plan）
2. approvals 加 alert_id（审核单直接关联告警）
3. approvals 加 kind（risk_approval / manual_assist）
4. alerts 加 resolved_by（处理人，≠审批人）
5. alert_policies 加 notify_config_id（通知渠道，关联 notification_configs）
6. notification_configs 加 im_template（IM 通知模板，按渠道各一套）
7. 新建 in_app_messages 表（站内消息中心）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1-3. approvals 批一字段
    op.alter_column('approvals', 'plan_id', existing_type=sa.Integer(), nullable=True)
    op.add_column('approvals', sa.Column('alert_id', sa.Integer(), nullable=True))
    op.add_column('approvals', sa.Column('kind', sa.String(length=20), server_default='risk_approval', nullable=False))
    op.create_index('ix_approvals_alert_id', 'approvals', ['alert_id'])
    op.create_index('ix_approvals_kind', 'approvals', ['kind'])

    # 4. alerts.resolved_by（处理人）
    op.add_column('alerts', sa.Column('resolved_by', sa.String(length=50), server_default='', nullable=False))

    # 5. alert_policies.notify_config_id（通知渠道）
    op.add_column('alert_policies', sa.Column('notify_config_id', sa.Integer(), nullable=True))

    # 6. notification_configs.im_template（IM 通知模板）
    op.add_column('notification_configs', sa.Column('im_template', sa.Text(), nullable=True))

    # 7. in_app_messages 表（站内消息中心）
    op.create_table(
        'in_app_messages',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), server_default='', nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('kind', sa.String(length=20), server_default='info', nullable=False),
        sa.Column('link', sa.String(length=500), server_default='', nullable=False),
        sa.Column('is_read', sa.Boolean(), server_default=sa.text('0'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_in_app_messages_user_id', 'in_app_messages', ['user_id'])
    op.create_index('ix_in_app_messages_is_read', 'in_app_messages', ['is_read'])


def downgrade() -> None:
    op.drop_index('ix_in_app_messages_is_read', table_name='in_app_messages')
    op.drop_index('ix_in_app_messages_user_id', table_name='in_app_messages')
    op.drop_table('in_app_messages')

    op.drop_column('notification_configs', 'im_template')
    op.drop_column('alert_policies', 'notify_config_id')
    op.drop_column('alerts', 'resolved_by')

    op.drop_index('ix_approvals_kind', table_name='approvals')
    op.drop_index('ix_approvals_alert_id', table_name='approvals')
    op.drop_column('approvals', 'kind')
    op.drop_column('approvals', 'alert_id')
    op.alter_column('approvals', 'plan_id', existing_type=sa.Integer(), nullable=False)
