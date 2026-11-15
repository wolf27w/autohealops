# -*- coding: utf-8 -*-
"""数据库审核机制（B12）：审核策略表 + 审核单表

Revision ID: b12db0a1c2d3
Revises: 8c1d4e6f2a0b
Create Date: 2026-11-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b12db0a1c2d3'
down_revision: Union[str, None] = '8c1d4e6f2a0b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 审核策略表：一个数据库连接一行
    op.create_table(
        'db_approval_policies',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('conn_id', sa.Integer(), nullable=False),
        sa.Column('need_query', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('need_write', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('need_ddl', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('need_export', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('updated_by', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_db_approval_policies_conn_id', 'db_approval_policies', ['conn_id'], unique=True)

    # 审核单表：query/write/ddl/export 四类操作的审批与执行记录
    op.create_table(
        'db_approval_requests',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('conn_id', sa.Integer(), nullable=False),
        sa.Column('conn_name', sa.String(length=128), nullable=False, server_default=''),
        sa.Column('db_name', sa.String(length=128), nullable=False, server_default=''),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='pending'),
        sa.Column('requested_by', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('requested_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('approved_by', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('reject_reason', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('execute_mode', sa.String(length=16), nullable=False, server_default='immediate'),
        sa.Column('scheduled_at', sa.DateTime(), nullable=True),
        sa.Column('executed_at', sa.DateTime(), nullable=True),
        sa.Column('result_summary', sa.Text(), nullable=False),
        sa.Column('download_token', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('download_expires_at', sa.DateTime(), nullable=True),
        sa.Column('download_used', sa.Boolean(), nullable=False, server_default=sa.text('0')),
    )
    op.create_index('ix_db_approval_requests_conn_id', 'db_approval_requests', ['conn_id'])
    op.create_index('ix_db_approval_requests_status', 'db_approval_requests', ['status'])
    op.create_index('ix_db_approval_requests_download_token', 'db_approval_requests', ['download_token'])


def downgrade() -> None:
    op.drop_index('ix_db_approval_requests_download_token', table_name='db_approval_requests')
    op.drop_index('ix_db_approval_requests_status', table_name='db_approval_requests')
    op.drop_index('ix_db_approval_requests_conn_id', table_name='db_approval_requests')
    op.drop_table('db_approval_requests')
    op.drop_index('ix_db_approval_policies_conn_id', table_name='db_approval_policies')
    op.drop_table('db_approval_policies')
