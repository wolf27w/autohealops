# -*- coding: utf-8 -*-
"""k8s_events 增加 owner_kind/owner_name：用 Pod 的 ownerReferences 精确关联工作负载

原来按 Pod 名前缀（`deploy-`）匹配，工作负载改名/自定义命名就对不上；
改用 ownerReferences（Pod → ReplicaSet → Deployment / StatefulSet / DaemonSet）精确关联。

Revision ID: b9c0d1e2f3a4
Revises: f7a8b9c0d1e2
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b9c0d1e2f3a4'
down_revision: Union[str, None] = 'f7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('k8s_events', sa.Column('owner_kind', sa.String(40), server_default='', nullable=False))
    op.add_column('k8s_events', sa.Column('owner_name', sa.String(200), server_default='', nullable=False))
    op.create_index('ix_k8s_events_owner', 'k8s_events', ['owner_kind', 'owner_name'])


def downgrade() -> None:
    op.drop_index('ix_k8s_events_owner', table_name='k8s_events')
    op.drop_column('k8s_events', 'owner_name')
    op.drop_column('k8s_events', 'owner_kind')
