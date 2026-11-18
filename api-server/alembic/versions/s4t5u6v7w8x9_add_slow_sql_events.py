# -*- coding: utf-8 -*-
"""新增 slow_sql_events 慢 SQL 明细表（一次执行一行，对齐阿里云慢日志）

Revision ID: s4t5u6v7w8x9
Revises: r1e2c3u4r5e6
Create Date: 2026-11-18 12:20:00.000000

- 表结构见 app/models/slow_sql_event.py
- 联合索引 (db_connection_id, start_time)：明细列表按连接 + 时间范围过滤
- 唯一键 (db_connection_id, thread_id, start_time, digest)：防重复采集
  （采集用 INSERT IGNORE；水位失效/重启回落时靠它去重）
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "s4t5u6v7w8x9"
down_revision: Union[str, None] = "r1e2c3u4r5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "slow_sql_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("db_connection_id", sa.Integer, nullable=False, index=True),
        sa.Column("digest", sa.String(64), nullable=False, server_default="", index=True),
        # TEXT 列不能带 server_default（MySQL 8 限制），默认值由 ORM 侧给
        sa.Column("sql_text", sa.Text, nullable=False),
        sa.Column("schema_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("start_time", sa.DateTime, nullable=False, index=True),
        sa.Column("exec_seconds", sa.Float, nullable=False, server_default="0"),
        sa.Column("lock_seconds", sa.Float, nullable=False, server_default="0"),
        sa.Column("rows_sent", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rows_examined", sa.Integer, nullable=False, server_default="0"),
        sa.Column("client_host", sa.String(100), nullable=False, server_default=""),
        sa.Column("db_user", sa.String(100), nullable=False, server_default=""),
        sa.Column("thread_id", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("tables", sa.String(500), nullable=False, server_default=""),
        sa.Column("tag", sa.String(100), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    # 联合索引：明细列表主查询 (db_connection_id, start_time)
    op.create_index(
        "ix_slow_sql_events_conn_start", "slow_sql_events",
        ["db_connection_id", "start_time"],
    )
    # 唯一键：防重复采集（INSERT IGNORE 语义）
    op.create_index(
        "uk_slow_sql_events_conn_thread_start_digest", "slow_sql_events",
        ["db_connection_id", "thread_id", "start_time", "digest"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uk_slow_sql_events_conn_thread_start_digest", table_name="slow_sql_events")
    op.drop_index("ix_slow_sql_events_conn_start", table_name="slow_sql_events")
    op.drop_table("slow_sql_events")
