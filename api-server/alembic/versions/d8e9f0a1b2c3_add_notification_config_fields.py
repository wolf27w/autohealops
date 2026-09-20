"""add notification config transport/feedback/stream fields

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-08-23 14:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8e9f0a1b2c3'
down_revision: Union[str, None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('notification_configs', sa.Column('transport_mode', sa.String(length=20), nullable=False, server_default='webhook'))
    op.add_column('notification_configs', sa.Column('feedback_mode', sa.String(length=20), nullable=False, server_default='text'))
    # MySQL 不允许 TEXT 列带 server_default，默认值由应用层（模型 default=""）提供
    op.add_column('notification_configs', sa.Column('stream_config', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('notification_configs', 'stream_config')
    op.drop_column('notification_configs', 'feedback_mode')
    op.drop_column('notification_configs', 'transport_mode')
