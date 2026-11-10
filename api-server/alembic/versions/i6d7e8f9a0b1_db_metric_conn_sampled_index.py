# -*- coding: utf-8 -*-
"""db_metric_snapshots 补 (db_connection_id, sampled_at) 索引（2026-11-10）

DB 监控页「指标趋势」查询：`WHERE db_connection_id=? AND sampled_at >= ?`，
原来只能走单列 db_connection_id 索引，扫 81 万行（实测 2.6~4.3s）；
补复合索引后 1 小时窗口 19ms、24 小时窗口 76ms。

Revision ID: i6d7e8f9a0b1
Revises: h5c6d7e8f9a0
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'i6d7e8f9a0b1'
down_revision: Union[str, None] = 'h5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _exists(table: str, index: str) -> bool:
    conn = op.get_bind()
    return bool(conn.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i"
    ), {"t": table, "i": index}).scalar())


def upgrade() -> None:
    if not _exists("db_metric_snapshots", "ix_dbms_conn_sampled"):
        op.execute("ALTER TABLE db_metric_snapshots "
                   "ADD INDEX ix_dbms_conn_sampled (db_connection_id, sampled_at), "
                   "ALGORITHM=INPLACE, LOCK=NONE")


def downgrade() -> None:
    if _exists("db_metric_snapshots", "ix_dbms_conn_sampled"):
        op.drop_index("ix_dbms_conn_sampled", table_name="db_metric_snapshots")
