# -*- coding: utf-8 -*-
"""集群内数据库连接表（通过 agent 查对方集群数据库）

Revision ID: a1b2c3d4e5f7
Revises: d5e6f7a8b9c0
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f7'
down_revision: Union[str, None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'cluster_db_connections',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('cluster_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(100), server_default='', nullable=False),
        sa.Column('db_type', sa.String(20), server_default='mysql', nullable=False),
        sa.Column('host', sa.String(200), nullable=False),
        sa.Column('port', sa.Integer(), server_default='3306', nullable=False),
        sa.Column('database', sa.String(100), server_default='', nullable=False),
        sa.Column('username', sa.String(100), server_default='', nullable=False),
        sa.Column('password_encrypted', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        mysql_charset='utf8mb4',
    )
    op.create_index('ix_cluster_db_connections_cluster_id', 'cluster_db_connections', ['cluster_id'])


def downgrade() -> None:
    op.drop_index('ix_cluster_db_connections_cluster_id', table_name='cluster_db_connections')
    op.drop_table('cluster_db_connections')
