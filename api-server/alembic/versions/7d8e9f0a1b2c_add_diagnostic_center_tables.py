"""add diagnostic center tables

Revision ID: 7d8e9f0a1b2c
Revises: 5c6d7e8f9a0b
Create Date: 2026-08-20 13:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7d8e9f0a1b2c'
down_revision: Union[str, None] = '5c6d7e8f9a0b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('diagnostic_tasks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('task_type', sa.String(length=10), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('target', sa.Text(), nullable=False),
        sa.Column('time_window_minutes', sa.Integer(), nullable=False),
        sa.Column('cron', sa.String(length=100), nullable=False),
        sa.Column('model_config_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('last_run_at', sa.DateTime(), nullable=True),
        sa.Column('next_run_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_diagnostic_tasks_task_type'), 'diagnostic_tasks', ['task_type'], unique=False)
    op.create_index(op.f('ix_diagnostic_tasks_status'), 'diagnostic_tasks', ['status'], unique=False)

    op.create_table('diagnostic_reports',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('task_type', sa.String(length=10), nullable=False),
        sa.Column('conclusion', sa.Text(), nullable=False),
        sa.Column('root_cause', sa.Text(), nullable=False),
        sa.Column('suggestion', sa.Text(), nullable=False),
        sa.Column('raw_output', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_diagnostic_reports_task_id'), 'diagnostic_reports', ['task_id'], unique=False)

    op.create_table('db_connections',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('db_type', sa.String(length=20), nullable=False),
        sa.Column('host', sa.String(length=200), nullable=False),
        sa.Column('port', sa.Integer(), nullable=False),
        sa.Column('database', sa.String(length=100), nullable=False),
        sa.Column('username', sa.String(length=100), nullable=False),
        sa.Column('password_encrypted', sa.String(length=500), nullable=False),
        sa.Column('slow_sql_threshold', sa.Float(), nullable=False),
        sa.Column('collect_interval', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_db_connections_db_type'), 'db_connections', ['db_type'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_db_connections_db_type'), table_name='db_connections')
    op.drop_table('db_connections')
    op.drop_index(op.f('ix_diagnostic_reports_task_id'), table_name='diagnostic_reports')
    op.drop_table('diagnostic_reports')
    op.drop_index(op.f('ix_diagnostic_tasks_status'), table_name='diagnostic_tasks')
    op.drop_index(op.f('ix_diagnostic_tasks_task_type'), table_name='diagnostic_tasks')
    op.drop_table('diagnostic_tasks')
