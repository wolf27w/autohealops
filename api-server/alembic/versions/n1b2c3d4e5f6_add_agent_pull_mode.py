# -*- coding: utf-8 -*-
"""R14 双接入：clusters 加 agent_mode / agent_pull_url / agent_pull_token_encrypted

Revision ID: n1b2c3d4e5f6
Revises: m1a2b3c4d5e6
Create Date: 2026-11-11
"""
from alembic import op
import sqlalchemy as sa

revision: str = 'n1b2c3d4e5f6'
down_revision = 'm1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('clusters', sa.Column('agent_mode', sa.String(10), nullable=False, server_default='push'))
    op.add_column('clusters', sa.Column('agent_pull_url', sa.String(200), nullable=False, server_default=''))
    op.add_column('clusters', sa.Column('agent_pull_token_encrypted', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('clusters', 'agent_pull_token_encrypted')
    op.drop_column('clusters', 'agent_pull_url')
    op.drop_column('clusters', 'agent_mode')
