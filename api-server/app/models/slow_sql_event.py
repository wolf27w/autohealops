# -*- coding: utf-8 -*-
"""慢 SQL 明细（一次执行一行）模型——对齐阿里云慢日志逐次执行记录

数据来源：performance_schema.events_statements_history_long（方案 A）。
与 `slow_sql_records`（按 digest 聚合）不同，本表一行 = 一次慢查询执行。
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SlowSQLEvent(Base):
    """慢 SQL 逐次执行明细（MySQL 直连采集）"""

    __tablename__ = "slow_sql_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    db_connection_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), default="", index=True)
    sql_text: Mapped[str] = mapped_column(Text, default="")
    schema_name: Mapped[str] = mapped_column(String(100), default="")
    start_time: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    exec_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    lock_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    rows_sent: Mapped[int] = mapped_column(Integer, default=0)
    rows_examined: Mapped[int] = mapped_column(Integer, default=0)
    client_host: Mapped[str] = mapped_column(String(100), default="")
    db_user: Mapped[str] = mapped_column(String(100), default="")
    thread_id: Mapped[int] = mapped_column(BigInteger, default=0)
    # 涉及的库表（从 SQL 粗提取，逗号分隔，最多 5 个）
    tables: Mapped[str] = mapped_column(String(500), default="")
    # 平台自定义标签（S2 打标能力，本轮恒为空）
    tag: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
