"""add duty_overrides (C6 值班换班/代班)

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-09-12 13:45:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'duty_overrides',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('date', sa.String(length=10), nullable=False),
        sa.Column('original_username', sa.String(length=50), nullable=True, server_default=''),
        sa.Column('substitute_username', sa.String(length=50), nullable=False),
        sa.Column('type', sa.String(length=10), nullable=True, server_default='swap'),
        sa.Column('note', sa.String(length=255), nullable=True, server_default=''),
        sa.Column('created_by', sa.String(length=50), nullable=True, server_default=''),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_duty_overrides_date', 'duty_overrides', ['date'])


def downgrade() -> None:
    op.drop_index('ix_duty_overrides_date', table_name='duty_overrides')
    op.drop_table('duty_overrides')
