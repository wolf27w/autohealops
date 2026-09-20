"""add resource_snapshots (cluster_id, namespace) index

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-12 10:30:00.000000

变更（批二·提速）：resource_snapshots 加 (cluster_id, namespace) 联合索引，
加速 namespace-list（distinct）与按命名空间查资源。
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_resource_snapshots_cluster_ns',
        'resource_snapshots',
        ['cluster_id', 'namespace'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_resource_snapshots_cluster_ns', table_name='resource_snapshots')
