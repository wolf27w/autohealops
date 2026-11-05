# -*- coding: utf-8 -*-
"""k8s 事件历史表（平台自存，弥补 k8s event-ttl 1 小时）

Revision ID: e6f7a8b9c0d1
Revises: a1b2c3d4e5f7
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e6f7a8b9c0d1'
down_revision: Union[str, None] = 'a1b2c3d4e5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'k8s_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('cluster_id', sa.Integer(), nullable=False),
        sa.Column('uid', sa.String(64), nullable=False),
        sa.Column('namespace', sa.String(100), server_default='', nullable=False),
        sa.Column('resource_kind', sa.String(40), server_default='', nullable=False),
        sa.Column('resource_name', sa.String(200), server_default='', nullable=False),
        sa.Column('reason', sa.String(100), server_default='', nullable=False),
        sa.Column('type', sa.String(20), server_default='', nullable=False),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('first_seen', sa.DateTime(), nullable=True),
        sa.Column('last_seen', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cluster_id', 'uid', name='uk_k8s_event'),
        mysql_charset='utf8mb4',
    )
    op.create_index('ix_k8s_events_cluster_id', 'k8s_events', ['cluster_id'])
    op.create_index('ix_k8s_events_namespace', 'k8s_events', ['namespace'])
    op.create_index('ix_k8s_events_resource_kind', 'k8s_events', ['resource_kind'])
    op.create_index('ix_k8s_events_resource_name', 'k8s_events', ['resource_name'])
    op.create_index('ix_k8s_events_last_seen', 'k8s_events', ['last_seen'])


def downgrade() -> None:
    for ix in ('ix_k8s_events_last_seen', 'ix_k8s_events_resource_name', 'ix_k8s_events_resource_kind',
               'ix_k8s_events_namespace', 'ix_k8s_events_cluster_id'):
        op.drop_index(ix, table_name='k8s_events')
    op.drop_table('k8s_events')
