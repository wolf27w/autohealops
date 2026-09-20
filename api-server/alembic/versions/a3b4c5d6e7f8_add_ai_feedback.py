"""add ai_feedback (C5 AI 反馈闭环)

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-09-12 12:05:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ai_feedback',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('alert_id', sa.Integer(), nullable=True),
        sa.Column('diagnosis_id', sa.Integer(), nullable=True),
        sa.Column('rating', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('comment', sa.String(length=500), nullable=True, server_default=''),
        sa.Column('username', sa.String(length=50), nullable=True, server_default=''),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_ai_feedback_alert_id', 'ai_feedback', ['alert_id'])


def downgrade() -> None:
    op.drop_index('ix_ai_feedback_alert_id', table_name='ai_feedback')
    op.drop_table('ai_feedback')
