"""add tickets + ticket_comments (N1 平台内部工单)

Revision ID: i2j3k4l5m6n7
Revises: h1i2j3k4l5m6
Create Date: 2026-09-20 11:20:00.000000

变更（N1 平台内部工单）：
1. 新增 tickets 表：平台内部工单（告警/审批可转单，也可手动建单）
2. 新增 ticket_comments 表：工单留言 + 流转/指派留痕

说明：工单独立于告警/审批状态机，不改动现有告警与审批机制。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'i2j3k4l5m6n7'
down_revision: Union[str, None] = 'h1i2j3k4l5m6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tickets',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('ticket_no', sa.String(length=32), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('source', sa.String(length=10), server_default='manual', nullable=False),
        sa.Column('alert_id', sa.Integer(), nullable=True),
        sa.Column('approval_id', sa.Integer(), nullable=True),
        sa.Column('cluster_id', sa.Integer(), nullable=True),
        sa.Column('namespace', sa.String(length=100), server_default='', nullable=False),
        sa.Column('resource_name', sa.String(length=200), server_default='', nullable=False),
        sa.Column('priority', sa.String(length=10), server_default='medium', nullable=False),
        sa.Column('status', sa.String(length=20), server_default='open', nullable=False),
        sa.Column('assignee_id', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('ticket_no'),
    )
    op.create_index('ix_tickets_ticket_no', 'tickets', ['ticket_no'])
    op.create_index('ix_tickets_source', 'tickets', ['source'])
    op.create_index('ix_tickets_alert_id', 'tickets', ['alert_id'])
    op.create_index('ix_tickets_approval_id', 'tickets', ['approval_id'])
    op.create_index('ix_tickets_cluster_id', 'tickets', ['cluster_id'])
    op.create_index('ix_tickets_priority', 'tickets', ['priority'])
    op.create_index('ix_tickets_status', 'tickets', ['status'])
    op.create_index('ix_tickets_assignee_id', 'tickets', ['assignee_id'])

    op.create_table(
        'ticket_comments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('ticket_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('username', sa.String(length=50), server_default='', nullable=False),
        sa.Column('type', sa.String(length=10), server_default='comment', nullable=False),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ticket_comments_ticket_id', 'ticket_comments', ['ticket_id'])
    op.create_index('ix_ticket_comments_type', 'ticket_comments', ['type'])


def downgrade() -> None:
    op.drop_index('ix_ticket_comments_type', table_name='ticket_comments')
    op.drop_index('ix_ticket_comments_ticket_id', table_name='ticket_comments')
    op.drop_table('ticket_comments')
    for idx in ('ix_tickets_assignee_id', 'ix_tickets_status', 'ix_tickets_priority',
                'ix_tickets_cluster_id', 'ix_tickets_approval_id', 'ix_tickets_alert_id',
                'ix_tickets_source', 'ix_tickets_ticket_no'):
        op.drop_index(idx, table_name='tickets')
    op.drop_table('tickets')
