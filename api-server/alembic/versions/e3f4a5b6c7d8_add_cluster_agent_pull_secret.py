# -*- coding: utf-8 -*-
"""clusters 增加 agent_pull_secret（接入时使用的镜像拉取凭据名，随集群记录）

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e3f4a5b6c7d8'
down_revision: Union[str, None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('clusters', sa.Column('agent_pull_secret', sa.String(200), server_default='', nullable=False))


def downgrade() -> None:
    op.drop_column('clusters', 'agent_pull_secret')
