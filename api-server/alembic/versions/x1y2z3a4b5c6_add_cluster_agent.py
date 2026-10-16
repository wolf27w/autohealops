# -*- coding: utf-8 -*-
"""Agent 接入（M3）：clusters 加 agent_token_hash / agent_status / agent_last_seen_at / agent_version

Revision ID: x1y2z3a4b5c6
Revises: c1d2e3f4a5b6
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'x1y2z3a4b5c6'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('clusters', sa.Column('agent_token_hash', sa.String(128), nullable=False, server_default=''))
    op.add_column('clusters', sa.Column('agent_status', sa.String(20), nullable=False, server_default=''))
    op.add_column('clusters', sa.Column('agent_last_seen_at', sa.DateTime(), nullable=True))
    op.add_column('clusters', sa.Column('agent_version', sa.String(50), nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('clusters', 'agent_version')
    op.drop_column('clusters', 'agent_last_seen_at')
    op.drop_column('clusters', 'agent_status')
    op.drop_column('clusters', 'agent_token_hash')
