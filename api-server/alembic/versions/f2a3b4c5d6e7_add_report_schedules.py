"""add report_schedules (C4 报表定时推送)

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-09-12 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'report_schedules',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('cron_expression', sa.String(length=50), nullable=False),
        sa.Column('notify_config_id', sa.Integer(), nullable=True),
        sa.Column('range_days', sa.Integer(), nullable=True, server_default='7'),
        sa.Column('enabled', sa.Boolean(), nullable=True, server_default=sa.text('1')),
        sa.Column('last_run_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_report_schedules_enabled', 'report_schedules', ['enabled'])


def downgrade() -> None:
    op.drop_index('ix_report_schedules_enabled', table_name='report_schedules')
    op.drop_table('report_schedules')
