# -*- coding: utf-8 -*-
"""集群接入三方式：clusters 加 source / api_server_url

Revision ID: a0b1c2d3e4f5
Revises: a2b3c4d5e6f7
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a0b1c2d3e4f5'
down_revision: Union[str, None] = 'a2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('clusters', sa.Column('source', sa.String(20), nullable=False, server_default='kubeconfig'))
    op.add_column('clusters', sa.Column('api_server_url', sa.String(500), nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('clusters', 'api_server_url')
    op.drop_column('clusters', 'source')
