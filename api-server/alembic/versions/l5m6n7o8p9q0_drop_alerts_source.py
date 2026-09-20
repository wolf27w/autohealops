"""drop alerts.source (去重：死列，语义与 resource_type 重叠且无代码读写)

Revision ID: l5m6n7o8p9q0
Revises: k4l5m6n7o8p9
Create Date: 2026-09-20 12:00:00.000000

背景：g1b2c3d4e5f6 为区分「集群/日志/DB」来源加了 alerts.source，但实际来源一直由
resource_type（pod/node/log/db）表达，source 从未被任何代码读写（模型也未映射）→ 删除。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'l5m6n7o8p9q0'
down_revision: Union[str, None] = 'k4l5m6n7o8p9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('alerts', 'source')


def downgrade() -> None:
    op.add_column('alerts', sa.Column('source', sa.String(length=10), server_default='', nullable=False))
