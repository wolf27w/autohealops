"""add diagnosis sessions and messages (AI 诊断助手)

Revision ID: c7d8e9f0a1b2
Revises: 9b1c2d3e4f5a
Create Date: 2026-08-23 14:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, None] = '9b1c2d3e4f5a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('diagnosis_sessions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('cluster_id', sa.Integer(), nullable=False),
        sa.Column('namespace', sa.String(length=100), nullable=False),
        sa.Column('pod_name', sa.String(length=200), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_diagnosis_sessions_user_id'), 'diagnosis_sessions', ['user_id'], unique=False)
    op.create_index(op.f('ix_diagnosis_sessions_cluster_id'), 'diagnosis_sessions', ['cluster_id'], unique=False)
    op.create_index(op.f('ix_diagnosis_sessions_status'), 'diagnosis_sessions', ['status'], unique=False)

    op.create_table('diagnosis_messages',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('tool_name', sa.String(length=50), nullable=False),
        sa.Column('tool_args', sa.Text(), nullable=False),
        sa.Column('tool_output', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_diagnosis_messages_session_id'), 'diagnosis_messages', ['session_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_diagnosis_messages_session_id'), table_name='diagnosis_messages')
    op.drop_table('diagnosis_messages')
    op.drop_index(op.f('ix_diagnosis_sessions_status'), table_name='diagnosis_sessions')
    op.drop_index(op.f('ix_diagnosis_sessions_cluster_id'), table_name='diagnosis_sessions')
    op.drop_index(op.f('ix_diagnosis_sessions_user_id'), table_name='diagnosis_sessions')
    op.drop_table('diagnosis_sessions')
