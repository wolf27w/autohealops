"""add cluster health check fields (连接检查：上次检查时间/错误)

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-09-12 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c5d6e7f8a9b0'
down_revision: Union[str, None] = 'b4c5d6e7f8a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('clusters', sa.Column('last_check_at', sa.DateTime(), nullable=True))
    op.add_column('clusters', sa.Column('last_check_error', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('clusters', 'last_check_error')
    op.drop_column('clusters', 'last_check_at')
