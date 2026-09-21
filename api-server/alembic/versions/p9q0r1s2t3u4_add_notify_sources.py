"""add notification_configs.sources (告警来源过滤)

Revision ID: p9q0r1s2t3u4
Revises: o8p9q0r1s2t3
Create Date: 2026-09-21 11:25:00.000000

背景：通知渠道原先只能按 scene（用途）+ namespaces 过滤，不能按「告警来源」区分。
新增 sources（JSON 数组：pod/node/log/db），空=全部来源。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'p9q0r1s2t3u4'
down_revision: Union[str, None] = 'o8p9q0r1s2t3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # MySQL 的 TEXT 列不支持 DEFAULT，故不加 server_default；存量行由 MySQL 隐式填 ''
    op.add_column('notification_configs', sa.Column('sources', sa.Text(), nullable=False))


def downgrade() -> None:
    op.drop_column('notification_configs', 'sources')
