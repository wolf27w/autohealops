"""add notify_config_id to log_alert_rules

Revision ID: u4v5w6x7y8z9
Revises: t3u4v5w6x7y8
Create Date: 2026-10-10 14:40:00.000000

变更（日志源告警）：log_alert_rules.notify_config_id 指定通知渠道
（空=走全平台统一告警通知策略；命中后发到该通知渠道，实现不同日志源不同群）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'u4v5w6x7y8z9'
down_revision: Union[str, None] = 't3u4v5w6x7y8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('log_alert_rules', sa.Column('notify_config_id', sa.Integer(), nullable=True))
    op.create_index('ix_log_alert_rules_notify_config_id', 'log_alert_rules', ['notify_config_id'])


def downgrade() -> None:
    op.drop_index('ix_log_alert_rules_notify_config_id', table_name='log_alert_rules')
    op.drop_column('log_alert_rules', 'notify_config_id')
