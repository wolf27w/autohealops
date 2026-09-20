# -*- coding: utf-8 -*-
"""数据库指标快照模型（诊断时实时采集，多次诊断形成趋势）"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DBMetricSnapshot(Base):
    """数据库指标快照（连接数/锁等待/表空间等，诊断时采集落库）"""

    __tablename__ = "db_metric_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    db_connection_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    # connections=连接数 / lock_wait=锁等待 / tablespace=表空间 / slow_sql=慢SQL 统计
    metric_type: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)  # 如 connections_used / db_size_gb
    value: Mapped[float] = mapped_column(Float, default=0.0)
    # 明细 JSON（如 Top 大表列表、锁等待详情）
    detail: Mapped[str] = mapped_column(Text, default="")
    sampled_at: Mapped[datetime] = mapped_column(DateTime, index=True, server_default=func.now())
