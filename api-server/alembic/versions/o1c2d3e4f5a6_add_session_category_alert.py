# -*- coding: utf-8 -*-
"""诊断会话加 category / alert_id（应用故障 → 助手 闭环）

Revision ID: o1c2d3e4f5a6
Revises: n1b2c3d4e5f6
Create Date: 2026-11-11
"""
from alembic import op
import sqlalchemy as sa

revision: str = 'o1c2d3e4f5a6'
down_revision = 'n1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('diagnosis_sessions', sa.Column('category', sa.String(20), nullable=False, server_default='app'))
    op.add_column('diagnosis_sessions', sa.Column('alert_id', sa.Integer(), nullable=True))
    op.create_index('ix_diagnosis_sessions_category', 'diagnosis_sessions', ['category'])
    op.create_index('ix_diagnosis_sessions_alert_id', 'diagnosis_sessions', ['alert_id'])


def downgrade() -> None:
    op.drop_index('ix_diagnosis_sessions_alert_id', table_name='diagnosis_sessions')
    op.drop_index('ix_diagnosis_sessions_category', table_name='diagnosis_sessions')
    op.drop_column('diagnosis_sessions', 'alert_id')
    op.drop_column('diagnosis_sessions', 'category')
