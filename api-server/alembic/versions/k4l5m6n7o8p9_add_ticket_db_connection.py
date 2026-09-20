"""add tickets.db_connection_id (N1 补充：DB 类工单按 db 权限)

Revision ID: k4l5m6n7o8p9
Revises: j3k4l5m6n7o8
Create Date: 2026-09-20 11:30:00.000000

变更：tickets 增加 db_connection_id（可空，带索引）—— 手动建单可关联数据库连接，
可见性/详情按 check_db_permission 收敛（与集群权限并列）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'k4l5m6n7o8p9'
down_revision: Union[str, None] = 'j3k4l5m6n7o8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tickets', sa.Column('db_connection_id', sa.Integer(), nullable=True))
    op.create_index('ix_tickets_db_connection_id', 'tickets', ['db_connection_id'])


def downgrade() -> None:
    op.drop_index('ix_tickets_db_connection_id', table_name='tickets')
    op.drop_column('tickets', 'db_connection_id')
