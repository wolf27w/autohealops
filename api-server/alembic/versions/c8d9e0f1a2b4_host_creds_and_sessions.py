# -*- coding: utf-8 -*-
"""R8：主机凭据保存（auth_type / 私钥内容 / 私钥口令）+ 会话留痕表

Revision ID: c8d9e0f1a2b4
Revises: 9f8e7d6c5b4a
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c8d9e0f1a2b4'
down_revision: Union[str, None] = '9f8e7d6c5b4a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # MySQL 的 TEXT 列不能带 server_default，故这些列用 nullable=True
    op.add_column('hosts', sa.Column('auth_type', sa.String(length=10), nullable=False,
                                     server_default='password'))
    op.add_column('hosts', sa.Column('ssh_key_encrypted', sa.Text(), nullable=True))
    op.add_column('hosts', sa.Column('ssh_key_passphrase_encrypted', sa.Text(), nullable=True))
    op.add_column('hosts', sa.Column('description', sa.String(length=300), nullable=False,
                                     server_default=''))

    op.create_table(
        'host_sessions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('host_id', sa.Integer(), nullable=False),
        sa.Column('host_name', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('source_ip', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='active'),
        sa.Column('started_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
        sa.Column('duration_sec', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('bytes_in', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('bytes_out', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('cols', sa.Integer(), nullable=False, server_default='80'),
        sa.Column('rows', sa.Integer(), nullable=False, server_default='24'),
        sa.Column('recorded', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('close_reason', sa.String(length=200), nullable=False, server_default=''),
    )
    op.create_index('ix_host_sessions_host_id', 'host_sessions', ['host_id'])
    op.create_index('ix_host_sessions_user_id', 'host_sessions', ['user_id'])

    op.create_table(
        'host_session_chunks',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('direction', sa.String(length=10), nullable=False, server_default='output'),
        sa.Column('ts_ms', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('data', sa.LargeBinary(), nullable=False),
    )
    op.create_index('ix_host_session_chunks_session_id', 'host_session_chunks', ['session_id'])


def downgrade() -> None:
    op.drop_index('ix_host_session_chunks_session_id', table_name='host_session_chunks')
    op.drop_table('host_session_chunks')
    op.drop_index('ix_host_sessions_user_id', table_name='host_sessions')
    op.drop_index('ix_host_sessions_host_id', table_name='host_sessions')
    op.drop_table('host_sessions')
    op.drop_column('hosts', 'description')
    op.drop_column('hosts', 'ssh_key_passphrase_encrypted')
    op.drop_column('hosts', 'ssh_key_encrypted')
    op.drop_column('hosts', 'auth_type')
