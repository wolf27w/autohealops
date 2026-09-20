"""add alert collaboration (assign / acknowledge / comments)

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-09-12 11:00:00.000000

变更（C1·告警协作）：
- alerts 加 assigned_to / assigned_at / acknowledged / acknowledged_by / acknowledged_at
- 新表 alert_comments（告警备注/协作记录）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('alerts', sa.Column('assigned_to', sa.String(length=50), nullable=True, server_default=''))
    op.add_column('alerts', sa.Column('assigned_at', sa.DateTime(), nullable=True))
    op.add_column('alerts', sa.Column('acknowledged', sa.Boolean(), nullable=True, server_default=sa.text('0')))
    op.add_column('alerts', sa.Column('acknowledged_by', sa.String(length=50), nullable=True, server_default=''))
    op.add_column('alerts', sa.Column('acknowledged_at', sa.DateTime(), nullable=True))
    op.create_index('ix_alerts_acknowledged', 'alerts', ['acknowledged'])
    op.create_table(
        'alert_comments',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('alert_id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=50), nullable=True, server_default=''),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_alert_comments_alert_id', 'alert_comments', ['alert_id'])


def downgrade() -> None:
    op.drop_index('ix_alert_comments_alert_id', table_name='alert_comments')
    op.drop_table('alert_comments')
    op.drop_index('ix_alerts_acknowledged', table_name='alerts')
    op.drop_column('alerts', 'acknowledged_at')
    op.drop_column('alerts', 'acknowledged_by')
    op.drop_column('alerts', 'acknowledged')
    op.drop_column('alerts', 'assigned_at')
    op.drop_column('alerts', 'assigned_to')
