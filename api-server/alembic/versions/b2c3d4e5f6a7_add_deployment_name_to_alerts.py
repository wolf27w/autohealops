"""add deployment_name to alerts

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-26 15:50:00.000000

变更：
1. alerts 表加 deployment_name 列（聚合键，去随机后缀的 Deployment 名）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('alerts', sa.Column('deployment_name', sa.String(length=200), nullable=True))
    op.create_index('ix_alerts_deployment_name', 'alerts', ['deployment_name'])


def downgrade() -> None:
    op.drop_index('ix_alerts_deployment_name', table_name='alerts')
    op.drop_column('alerts', 'deployment_name')
