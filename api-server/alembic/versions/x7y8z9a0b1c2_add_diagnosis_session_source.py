# -*- coding: utf-8 -*-
"""主动诊断（应用巡检）增加来源字段

新增 `diagnosis_sessions.source`：
- agent（默认）：用户在应用诊断助手主动发起
- proactive：平台巡检自动发起（异常但未告警）

Revision ID: x7y8z9a0b1c2
Revises: w6x7y8z9a0b1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'x7y8z9a0b1c2'
down_revision: Union[str, None] = 'w6x7y8z9a0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'diagnosis_sessions',
        sa.Column('source', sa.String(length=20), nullable=False, server_default='agent'),
    )
    op.create_index('ix_diagnosis_sessions_source', 'diagnosis_sessions', ['source'])


def downgrade() -> None:
    op.drop_index('ix_diagnosis_sessions_source', table_name='diagnosis_sessions')
    op.drop_column('diagnosis_sessions', 'source')
