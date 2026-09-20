# -*- coding: utf-8 -*-
"""Pod 指标采样模型（executor 每分钟全量采样，保留 3 天）"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PodMetric(Base):
    """Pod 指标采样（CPU/内存/网络）"""

    __tablename__ = "pod_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    namespace: Mapped[str] = mapped_column(String(100), index=True)
    pod_name: Mapped[str] = mapped_column(String(200), index=True)
    cpu_usage: Mapped[float] = mapped_column(Float, default=0.0)        # 单位：core
    memory_usage: Mapped[int] = mapped_column(BigInteger, default=0)    # 单位：byte
    network_rx: Mapped[int] = mapped_column(BigInteger, default=0)      # 单位：byte（累计）
    network_tx: Mapped[int] = mapped_column(BigInteger, default=0)      # 单位：byte（累计）
    sampled_at: Mapped[datetime] = mapped_column(DateTime, index=True, server_default=func.now())
