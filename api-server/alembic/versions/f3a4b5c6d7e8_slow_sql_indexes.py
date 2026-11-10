# -*- coding: utf-8 -*-
"""慢 SQL 优化：补两个复合索引（2026-11-10）

1. `db_metric_snapshots (metric_name, db_connection_id, id)`
   - 仪表盘「每库每指标取最新一条」：GROUP BY metric_name, db_connection_id + MAX(id)
     与索引顺序一致 → MySQL loose index scan（实测全表 878k 行扫描 → 2ms）
   - 历史查询 `WHERE db_connection_id=? AND metric_name=? ORDER BY id DESC` 同样命中
2. `quota_snapshots (namespace, cluster_id, id)`
   - 「每集群最新配额」：namespace 等值 + GROUP BY cluster_id + MAX(id) → 索引覆盖

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_exists(table: str, index: str) -> bool:
    conn = op.get_bind()
    row = conn.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i"
    ), {"t": table, "i": index}).scalar()
    return bool(row)


def upgrade() -> None:
    if not _index_exists("db_metric_snapshots", "ix_dbms_metric_conn_id"):
        # 大表在线加索引，不锁写
        op.execute("ALTER TABLE db_metric_snapshots "
                   "ADD INDEX ix_dbms_metric_conn_id (metric_name, db_connection_id, id), "
                   "ALGORITHM=INPLACE, LOCK=NONE")
    if not _index_exists("quota_snapshots", "ix_quota_ns_cluster_id"):
        op.execute("ALTER TABLE quota_snapshots "
                   "ADD INDEX ix_quota_ns_cluster_id (namespace, cluster_id, id), "
                   "ALGORITHM=INPLACE, LOCK=NONE")


def downgrade() -> None:
    if _index_exists("quota_snapshots", "ix_quota_ns_cluster_id"):
        op.drop_index("ix_quota_ns_cluster_id", table_name="quota_snapshots")
    if _index_exists("db_metric_snapshots", "ix_dbms_metric_conn_id"):
        op.drop_index("ix_dbms_metric_conn_id", table_name="db_metric_snapshots")
