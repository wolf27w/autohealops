# -*- coding: utf-8 -*-
"""R12：应用故障分类——log_alert_rules 加 fault_type/category/priority/sample_pattern；alerts 加 rule_priority

Revision ID: d1e2f3a4b5c6
Revises: c8d9e0f1a2b4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'c8d9e0f1a2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('log_alert_rules', sa.Column('fault_type', sa.String(length=50), nullable=False,
                                               server_default='LogKeyword'))
    op.add_column('log_alert_rules', sa.Column('category', sa.String(length=20), nullable=False,
                                               server_default='app'))
    op.add_column('log_alert_rules', sa.Column('priority', sa.Integer(), nullable=False, server_default='100'))
    op.add_column('log_alert_rules', sa.Column('sample_pattern', sa.String(length=300), nullable=False,
                                               server_default=''))
    op.add_column('alerts', sa.Column('rule_priority', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('alerts', 'rule_priority')
    op.drop_column('log_alert_rules', 'sample_pattern')
    op.drop_column('log_alert_rules', 'priority')
    op.drop_column('log_alert_rules', 'category')
    op.drop_column('log_alert_rules', 'fault_type')
