# -*- coding: utf-8 -*-
"""告警/工单/数据库连接增加「环境」维度（R24），2026-11-06

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e9
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, None] = "f3a4b5c6d7e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("db_connections", sa.Column("env_tag", sa.String(50), nullable=False, server_default=""))
    op.add_column("alerts", sa.Column("env", sa.String(50), nullable=False, server_default=""))
    op.add_column("tickets", sa.Column("env", sa.String(50), nullable=False, server_default=""))
    op.create_index("ix_alerts_env", "alerts", ["env"])
    op.create_index("ix_tickets_env", "tickets", ["env"])

    # 回填：集群告警取 clusters.env_tag；数据库告警取连接的 env_tag（与名称匹配）
    op.execute("""
        UPDATE alerts a JOIN clusters c ON a.cluster_id = c.id
        SET a.env = COALESCE(NULLIF(c.env_tag, ''), c.name)
        WHERE a.env = '' AND a.cluster_id > 0
    """)
    op.execute("""
        UPDATE alerts a JOIN db_connections d ON a.resource_name = d.name
        SET a.env = COALESCE(NULLIF(d.env_tag, ''), '')
        WHERE a.env = '' AND a.resource_type = 'db'
    """)
    # 工单：按关联告警回填，其次按 cluster 回填
    op.execute("""
        UPDATE tickets t JOIN alerts a ON t.alert_id = a.id
        SET t.env = a.env WHERE t.env = '' AND a.env <> ''
    """)
    op.execute("""
        UPDATE tickets t JOIN clusters c ON t.cluster_id = c.id
        SET t.env = COALESCE(NULLIF(c.env_tag, ''), c.name)
        WHERE t.env = '' AND t.cluster_id > 0
    """)


def downgrade() -> None:
    op.drop_index("ix_tickets_env", table_name="tickets")
    op.drop_index("ix_alerts_env", table_name="alerts")
    op.drop_column("tickets", "env")
    op.drop_column("alerts", "env")
    op.drop_column("db_connections", "env_tag")
