# -*- coding: utf-8 -*-
"""凭据到期治理：clusters 加 cred_type / cred_expires_at

Revision ID: c1d2e3f4a5b6
Revises: a0b1c2d3e4f5
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, None] = 'a0b1c2d3e4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('clusters', sa.Column('cred_type', sa.String(20), nullable=False, server_default='unknown'))
    op.add_column('clusters', sa.Column('cred_expires_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('clusters', 'cred_expires_at')
    op.drop_column('clusters', 'cred_type')
