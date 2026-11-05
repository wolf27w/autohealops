# -*- coding: utf-8 -*-
"""clusters 增加 agent_namespace / agent_image（按集群记录接入时使用的命名空间与镜像）

背景（老板 2026-11-05）：接入地址里要能带镜像地址和 namespace，按环境自行修改。
自定义 namespace 后，agent 升级/版本管理必须用该集群自己的 ns，不能再用全局默认。

Revision ID: d2e3f4a5b6c7
Revises: b9c0d1e2f3a4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, None] = 'b9c0d1e2f3a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('clusters', sa.Column('agent_namespace', sa.String(100), server_default='', nullable=False))
    op.add_column('clusters', sa.Column('agent_image', sa.String(200), server_default='', nullable=False))


def downgrade() -> None:
    op.drop_column('clusters', 'agent_image')
    op.drop_column('clusters', 'agent_namespace')
