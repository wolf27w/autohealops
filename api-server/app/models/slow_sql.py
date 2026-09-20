# -*- coding: utf-8 -*-
"""慢 SQL 统计模型"""
from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SlowSQLRecord(Base):
    """慢 SQL 统计（按 digest 聚合）"""

    __tablename__ = "slow_sql_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    db_connection_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), default="", index=True)
    sql_text: Mapped[str] = mapped_column(Text, default="")
    schema_name: Mapped[str] = mapped_column(String(100), default="")
    exec_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    max_latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    sampled_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
