"""add approval escalation fields

Revision ID: 5c6d7e8f9a0b
Revises: 8b7c3d4e5f6a
Create Date: 2026-08-20 13:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5c6d7e8f9a0b'
down_revision: Union[str, None] = '8b7c3d4e5f6a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('approvals', sa.Column('current_approver_index', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('approvals', sa.Column('last_notified_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('approvals', 'last_notified_at')
    op.drop_column('approvals', 'current_approver_index')
