# -*- coding: utf-8 -*-
"""诊断失败记录表（老板 2026-10-29：诊断失败要单独记录）

Revision ID: d5e6f7a8b9c0
Revises: z3a4b5c6d7e8
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, None] = 'z3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'diagnosis_failures',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('alert_id', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cluster_id', sa.Integer(), nullable=True),
        sa.Column('namespace', sa.Text(), nullable=True),
        sa.Column('resource_type', sa.Text(), nullable=True),
        sa.Column('resource_name', sa.Text(), nullable=True),
        sa.Column('fault_type', sa.Text(), nullable=True),
        sa.Column('stage', sa.Text(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('model', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        mysql_charset='utf8mb4',
    )
    op.create_index('ix_diagnosis_failures_alert_id', 'diagnosis_failures', ['alert_id'])
    op.create_index('ix_diagnosis_failures_cluster_id', 'diagnosis_failures', ['cluster_id'])
    op.create_index('ix_diagnosis_failures_fault_type', 'diagnosis_failures', ['fault_type'], mysql_length=50)
    op.create_index('ix_diagnosis_failures_stage', 'diagnosis_failures', ['stage'], mysql_length=20)
    op.create_index('ix_diagnosis_failures_reason', 'diagnosis_failures', ['reason'], mysql_length=20)
    op.create_index('ix_diagnosis_failures_created_at', 'diagnosis_failures', ['created_at'])


def downgrade() -> None:
    op.drop_table('diagnosis_failures')
