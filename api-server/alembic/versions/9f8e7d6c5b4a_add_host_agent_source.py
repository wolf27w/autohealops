# -*- coding: utf-8 -*-
"""主机 R7：source/cluster_id/source_key/last_metrics（agent SSH 代采外部 VM），2026-11-09

Revision ID: 9f8e7d6c5b4a
Revises: 5f6a7b8c9d0e
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "9f8e7d6c5b4a"
down_revision: Union[str, None] = "5f6a7b8c9d0e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("hosts", sa.Column("source", sa.String(10), nullable=False, server_default="direct"))
    op.add_column("hosts", sa.Column("cluster_id", sa.Integer(), nullable=True))
    op.add_column("hosts", sa.Column("source_key", sa.String(100), nullable=False, server_default=""))
    op.add_column("hosts", sa.Column("last_metrics", sa.Text(), nullable=True))
    op.create_index("ix_hosts_cluster_id", "hosts", ["cluster_id"])
    op.create_unique_constraint("uq_hosts_agent_key", "hosts", ["cluster_id", "source_key"])


def downgrade() -> None:
    op.drop_constraint("uq_hosts_agent_key", "hosts", type_="unique")
    op.drop_index("ix_hosts_cluster_id", table_name="hosts")
    op.drop_column("hosts", "last_metrics")
    op.drop_column("hosts", "source_key")
    op.drop_column("hosts", "cluster_id")
    op.drop_column("hosts", "source")
