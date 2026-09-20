"""add im_groups + users.feishu_open_id

Revision ID: b1c2d3e4f5a6
Revises: a5b6c7d8e9f0
Create Date: 2026-08-25 16:00:00.000000

变更：
1. 新表 im_groups（消息群：审批应用群 chat_id / 告警 webhook 群）
2. users 加 feishu_open_id（群消息 @人 + 长连接回调身份映射）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = 'a5b6c7d8e9f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'im_groups',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('channel_type', sa.String(length=20), nullable=False, index=True),
        sa.Column('chat_id', sa.String(length=200), default=''),
        sa.Column('webhook_url', sa.String(length=500), default=''),
        sa.Column('is_active', sa.Boolean(), default=True, index=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.add_column('users', sa.Column('feishu_open_id', sa.String(length=100), default='', nullable=True))

    # 更新默认流程节点为群绑定格式（group → group_id/at_duty）
    op.execute(
        sa.text(
            "UPDATE approval_flows SET nodes = :nodes WHERE is_default = 1"
        ).bindparams(
            nodes=(
                '[{"type": "duty", "timeout": 20, "approve_count": 0, "on_empty": "next", "group_id": 0, "at_duty": false},'
                '{"type": "group_all", "timeout": 30, "approve_count": 0, "group_id": 0, "include_admin": true, "at_duty": false},'
                '{"type": "admin", "timeout": 60, "approve_count": 0, "group_id": 0, "at_duty": false}]'
            )
        )
    )


def downgrade() -> None:
    op.drop_column('users', 'feishu_open_id')
    op.drop_table('im_groups')
