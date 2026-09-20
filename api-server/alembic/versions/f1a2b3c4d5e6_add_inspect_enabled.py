"""add inspect_enabled to db_connections and clusters

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-09-18 12:15:00.000000

变更（#7/#8）：
- db_connections.inspect_enabled：数据库巡检开关（按库）
- clusters.inspect_enabled：集群巡检开关（按集群）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'e0f1a2b3c4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('db_connections', sa.Column('inspect_enabled', sa.Boolean(), nullable=False, server_default=sa.text('1')))
    op.add_column('clusters', sa.Column('inspect_enabled', sa.Boolean(), nullable=False, server_default=sa.text('1')))


def downgrade() -> None:
    op.drop_column('clusters', 'inspect_enabled')
    op.drop_column('db_connections', 'inspect_enabled')
