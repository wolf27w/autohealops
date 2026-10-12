"""add alert_silences table (P2-R11)

Revision ID: w6x7y8z9a0b1
Revises: v5w6x7y8z9a0
Create Date: 2026-10-12 21:30:00.000000

新增「告警屏蔽」表：Alertmanager 风格匹配条件（matchers JSON），命中时只拦通知、不丢数据。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'w6x7y8z9a0b1'
down_revision: Union[str, None] = 'v5w6x7y8z9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'alert_silences',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(length=120), nullable=False, server_default=''),
        sa.Column('matchers', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('starts_at', sa.DateTime(), nullable=False),
        sa.Column('ends_at', sa.DateTime(), nullable=False),
        sa.Column('comment', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('created_by', sa.String(length=100), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('alert_silences')
