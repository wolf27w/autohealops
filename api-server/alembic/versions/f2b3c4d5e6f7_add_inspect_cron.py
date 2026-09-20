"""add inspect_cron to db_connections and clusters

Revision ID: f2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-09-18 12:30:00.000000

变更（#7/#8 巡检定时）：db_connections.inspect_cron / clusters.inspect_cron
（cron 表达式，空=不定时，仅手动点击巡检）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f2b3c4d5e6f7'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('db_connections', sa.Column('inspect_cron', sa.String(100), nullable=False, server_default=''))
    op.add_column('clusters', sa.Column('inspect_cron', sa.String(100), nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('clusters', 'inspect_cron')
    op.drop_column('db_connections', 'inspect_cron')
