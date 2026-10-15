# -*- coding: utf-8 -*-
"""主机/虚拟机纳管（N11）

Revision ID: z9a0b1c2d3e4
Revises: y8z9a0b1c2d3
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'z9a0b1c2d3e4'
down_revision: Union[str, None] = 'y8z9a0b1c2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'hosts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('address', sa.String(length=200), nullable=False),
        sa.Column('ssh_port', sa.Integer(), nullable=False, server_default='22'),
        sa.Column('ssh_user', sa.String(length=50), nullable=False, server_default='root'),
        sa.Column('ssh_password_encrypted', sa.Text(), nullable=False),
        sa.Column('ssh_key_path', sa.String(length=300), nullable=False, server_default=''),
        sa.Column('process_paths', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='unknown'),
        sa.Column('last_seen', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_hosts_name', 'hosts', ['name'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_hosts_name', table_name='hosts')
    op.drop_table('hosts')
