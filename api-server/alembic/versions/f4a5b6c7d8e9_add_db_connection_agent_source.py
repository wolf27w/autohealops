# -*- coding: utf-8 -*-
"""db_connections 增加 agent 来源字段（R6，2026-11-06）

- source: direct（平台直连）/ agent（agent 侧配置后上报，平台不存凭据）
- cluster_id + agent_db_key：agent 库的归属与唯一键（改名同步、下线标记都靠它）
- last_error / last_seen_at：最近一次采集状态

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-11-06 13:10:00
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, None] = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("db_connections", sa.Column("source", sa.String(20), nullable=False, server_default="direct"))
    op.add_column("db_connections", sa.Column("cluster_id", sa.Integer(), nullable=True))
    op.add_column("db_connections", sa.Column("agent_db_key", sa.String(100), nullable=True))
    op.add_column("db_connections", sa.Column("last_error", sa.String(255), nullable=False, server_default=""))
    op.add_column("db_connections", sa.Column("last_seen_at", sa.DateTime(), nullable=True))
    op.create_index("uk_dbconn_agent", "db_connections", ["cluster_id", "agent_db_key"], unique=True)


def downgrade() -> None:
    op.drop_index("uk_dbconn_agent", table_name="db_connections")
    op.drop_column("db_connections", "last_seen_at")
    op.drop_column("db_connections", "last_error")
    op.drop_column("db_connections", "agent_db_key")
    op.drop_column("db_connections", "cluster_id")
    op.drop_column("db_connections", "source")
