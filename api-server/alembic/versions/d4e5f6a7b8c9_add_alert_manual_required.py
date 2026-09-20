"""add alert manual_required

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-10 15:05:00.000000

变更：
1. alerts 加 manual_required（需人工协助标记，列表特别标注）
2. alerts 加 manual_reason（转人工原因，简短展示）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('alerts', sa.Column('manual_required', sa.Boolean(), server_default=sa.text('0'), nullable=False))
    op.add_column('alerts', sa.Column('manual_reason', sa.String(length=255), server_default='', nullable=False))
    op.create_index('ix_alerts_manual_required', 'alerts', ['manual_required'])


def downgrade() -> None:
    op.drop_index('ix_alerts_manual_required', table_name='alerts')
    op.drop_column('alerts', 'manual_reason')
    op.drop_column('alerts', 'manual_required')
