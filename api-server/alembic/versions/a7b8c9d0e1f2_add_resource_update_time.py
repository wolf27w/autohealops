"""add resource_snapshots update_time

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-09-11 17:50:00.000000

变更（集群管理）：resource_snapshots 加 update_time（工作负载最后更新时间，k8s）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('resource_snapshots', sa.Column('update_time', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('resource_snapshots', 'update_time')
