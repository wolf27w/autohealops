"""add notification_configs.encrypt_key (飞书 Encrypt Key 模式验签)

Revision ID: d9e0f1a2b3c4
Revises: d7e8f9a0b1c2
Create Date: 2026-09-12 14:38:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd9e0f1a2b3c4'
down_revision: Union[str, None] = 'd7e8f9a0b1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('notification_configs', sa.Column('encrypt_key', sa.String(length=200), nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('notification_configs', 'encrypt_key')
