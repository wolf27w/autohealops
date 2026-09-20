"""add project permission models

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-08-24 16:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f0a1b2c3d4e5'
down_revision: Union[str, None] = 'e9f0a1b2c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'projects',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(length=100), nullable=False, unique=True),
        sa.Column('description', sa.String(length=500), default=''),
        sa.Column('created_by', sa.Integer(), default=0),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        'project_cluster_ns',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('project_id', sa.Integer(), nullable=False, index=True),
        sa.Column('cluster_id', sa.Integer(), nullable=False, index=True),
        sa.Column('namespace', sa.String(length=100), nullable=False, index=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        'project_db_connections',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('project_id', sa.Integer(), nullable=False, index=True),
        sa.Column('db_connection_id', sa.Integer(), nullable=False, index=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_table(
        'user_project_permissions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), nullable=False, index=True),
        sa.Column('project_id', sa.Integer(), nullable=False, index=True),
        sa.Column('permission', sa.String(length=20), default='read', index=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('user_project_permissions')
    op.drop_table('project_db_connections')
    op.drop_table('project_cluster_ns')
    op.drop_table('projects')
