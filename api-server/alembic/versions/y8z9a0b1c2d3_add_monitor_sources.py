# -*- coding: utf-8 -*-
"""监控数据源（按集群覆盖 Prometheus 地址）

Revision ID: y8z9a0b1c2d3
Revises: x7y8z9a0b1c2
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'y8z9a0b1c2d3'
down_revision: Union[str, None] = 'x7y8z9a0b1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'monitor_sources',
        sa.Column('cluster_id', sa.Integer(), primary_key=True),
        sa.Column('url', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('monitor_sources')
