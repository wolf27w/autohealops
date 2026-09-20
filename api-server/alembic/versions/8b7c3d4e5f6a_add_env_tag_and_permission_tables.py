"""add env_tag and permission tables

Revision ID: 8b7c3d4e5f6a
Revises: 9a1c2b3d4e5f
Create Date: 2026-08-20 12:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8b7c3d4e5f6a'
down_revision: Union[str, None] = '9a1c2b3d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 集群加环境标签
    op.add_column('clusters', sa.Column('env_tag', sa.String(length=20), nullable=False, server_default='prod'))
    op.create_index(op.f('ix_clusters_env_tag'), 'clusters', ['env_tag'], unique=False)

    # 集群授权表
    op.create_table('user_cluster_permissions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('cluster_id', sa.Integer(), nullable=False),
        sa.Column('namespace', sa.String(length=100), nullable=False),
        sa.Column('permission', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_cluster_permissions_user_id'), 'user_cluster_permissions', ['user_id'], unique=False)
    op.create_index(op.f('ix_user_cluster_permissions_cluster_id'), 'user_cluster_permissions', ['cluster_id'], unique=False)
    op.create_index(op.f('ix_user_cluster_permissions_namespace'), 'user_cluster_permissions', ['namespace'], unique=False)
    op.create_index(op.f('ix_user_cluster_permissions_permission'), 'user_cluster_permissions', ['permission'], unique=False)

    # 数据库连接授权表
    op.create_table('user_db_connections',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('db_connection_id', sa.Integer(), nullable=False),
        sa.Column('permission', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_db_connections_user_id'), 'user_db_connections', ['user_id'], unique=False)
    op.create_index(op.f('ix_user_db_connections_db_connection_id'), 'user_db_connections', ['db_connection_id'], unique=False)
    op.create_index(op.f('ix_user_db_connections_permission'), 'user_db_connections', ['permission'], unique=False)

    # 告警订阅表
    op.create_table('alert_subscriptions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('cluster_id', sa.Integer(), nullable=False),
        sa.Column('namespace', sa.String(length=100), nullable=False),
        sa.Column('subscribe_type', sa.String(length=10), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_alert_subscriptions_user_id'), 'alert_subscriptions', ['user_id'], unique=False)
    op.create_index(op.f('ix_alert_subscriptions_cluster_id'), 'alert_subscriptions', ['cluster_id'], unique=False)
    op.create_index(op.f('ix_alert_subscriptions_namespace'), 'alert_subscriptions', ['namespace'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_alert_subscriptions_namespace'), table_name='alert_subscriptions')
    op.drop_index(op.f('ix_alert_subscriptions_cluster_id'), table_name='alert_subscriptions')
    op.drop_index(op.f('ix_alert_subscriptions_user_id'), table_name='alert_subscriptions')
    op.drop_table('alert_subscriptions')
    op.drop_index(op.f('ix_user_db_connections_permission'), table_name='user_db_connections')
    op.drop_index(op.f('ix_user_db_connections_db_connection_id'), table_name='user_db_connections')
    op.drop_index(op.f('ix_user_db_connections_user_id'), table_name='user_db_connections')
    op.drop_table('user_db_connections')
    op.drop_index(op.f('ix_user_cluster_permissions_permission'), table_name='user_cluster_permissions')
    op.drop_index(op.f('ix_user_cluster_permissions_namespace'), table_name='user_cluster_permissions')
    op.drop_index(op.f('ix_user_cluster_permissions_cluster_id'), table_name='user_cluster_permissions')
    op.drop_index(op.f('ix_user_cluster_permissions_user_id'), table_name='user_cluster_permissions')
    op.drop_table('user_cluster_permissions')
    op.drop_index(op.f('ix_clusters_env_tag'), table_name='clusters')
    op.drop_column('clusters', 'env_tag')
