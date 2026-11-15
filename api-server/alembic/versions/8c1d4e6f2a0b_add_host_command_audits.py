# -*- coding: utf-8 -*-
"""主机命令审计表（B8：主机终端命令审计 + 危险命令拦截）

Revision ID: 8c1d4e6f2a0b
Revises: bfe08be41a5b
Create Date: 2026-11-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '8c1d4e6f2a0b'
down_revision: Union[str, None] = 'bfe08be41a5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'host_command_audits',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('host_id', sa.Integer(), nullable=False),
        sa.Column('host_name', sa.String(length=128), nullable=False, server_default=''),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('session_id', sa.Integer(), nullable=True),
        sa.Column('command', sa.Text(), nullable=False),
        sa.Column('risk', sa.String(length=16), nullable=False, server_default='low'),
        sa.Column('action', sa.String(length=16), nullable=False, server_default='allowed'),
        sa.Column('reason', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('client_ip', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_host_command_audits_host_id', 'host_command_audits', ['host_id'])
    op.create_index('ix_host_command_audits_user_id', 'host_command_audits', ['user_id'])
    op.create_index('ix_host_command_audits_created_at', 'host_command_audits', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_host_command_audits_created_at', table_name='host_command_audits')
    op.drop_index('ix_host_command_audits_user_id', table_name='host_command_audits')
    op.drop_index('ix_host_command_audits_host_id', table_name='host_command_audits')
    op.drop_table('host_command_audits')
