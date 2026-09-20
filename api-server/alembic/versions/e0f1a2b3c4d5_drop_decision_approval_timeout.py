"""drop decision_policies.approval_timeout

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-09-15 13:45:00.000000

变更（P8）：审批流程已改为「无超时」语义（值班人 40 分钟未处理 → 通知所有人，单子不关；
关联告警自愈则自动解除），decision_policies.approval_timeout（旧「审批超时(秒)」）已无任何代码使用，删除。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e0f1a2b3c4d5'
down_revision: Union[str, None] = 'd9e0f1a2b3c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('decision_policies', 'approval_timeout')


def downgrade() -> None:
    op.add_column(
        'decision_policies',
        sa.Column('approval_timeout', sa.Integer(), nullable=False, server_default='1800'),
    )
