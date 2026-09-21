"""add notification_configs app-bot mode fields (飞书应用机器人)

Revision ID: o8p9q0r1s2t3
Revises: n7o8p9q0r1s2
Create Date: 2026-09-21 11:00:00.000000

背景：通知渠道原先只能填「群机器人 webhook」。老板要求支持「应用机器人」模式：
在通知渠道里填飞书自建应用的 App ID/Secret（+ 接收方），消息由该应用经 IM API 发出。
新增 send_mode（webhook/app）+ app_id/app_secret/receive_id/receive_id_type。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'o8p9q0r1s2t3'
down_revision: Union[str, None] = 'n7o8p9q0r1s2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('notification_configs', sa.Column('send_mode', sa.String(length=20), server_default='webhook', nullable=False))
    op.add_column('notification_configs', sa.Column('app_id', sa.String(length=100), server_default='', nullable=False))
    op.add_column('notification_configs', sa.Column('app_secret', sa.String(length=200), server_default='', nullable=False))
    op.add_column('notification_configs', sa.Column('receive_id', sa.String(length=200), server_default='', nullable=False))
    op.add_column('notification_configs', sa.Column('receive_id_type', sa.String(length=20), server_default='chat_id', nullable=False))


def downgrade() -> None:
    op.drop_column('notification_configs', 'receive_id_type')
    op.drop_column('notification_configs', 'receive_id')
    op.drop_column('notification_configs', 'app_secret')
    op.drop_column('notification_configs', 'app_id')
    op.drop_column('notification_configs', 'send_mode')
