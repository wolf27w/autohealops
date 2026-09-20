"""add cluster rbac fields

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-11 13:06:00.000000

变更（专项 B · 云原生 RBAC）：
1. clusters 加 rbac_enabled（是否启用 impersonation）
2. clusters 加 rbac_mode（off/rbac/fallback）
3. clusters 加 rbac_status（接入结果说明）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('clusters', sa.Column('rbac_enabled', sa.Boolean(), server_default=sa.text('0'), nullable=False))
    op.add_column('clusters', sa.Column('rbac_mode', sa.String(length=20), server_default='off', nullable=False))
    op.add_column('clusters', sa.Column('rbac_status', sa.String(length=255), server_default='', nullable=False))


def downgrade() -> None:
    op.drop_column('clusters', 'rbac_status')
    op.drop_column('clusters', 'rbac_mode')
    op.drop_column('clusters', 'rbac_enabled')
