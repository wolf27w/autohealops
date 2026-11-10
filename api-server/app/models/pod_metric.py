# -*- coding: utf-8 -*-
"""Pod 指标采样模型（executor/agent 每分钟全量采样；原始保留 24h，5 分钟聚合保留 7 天）"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PodMetric(Base):
    """Pod 指标采样（CPU/内存/网络）

    - 唯一键 `uk_pod_metric_sample`：同一 (集群, 命名空间, Pod, 采样时间) 只留一行，
      写入侧统一走 `ON DUPLICATE KEY UPDATE`（R27-2），重试/补采天然幂等。
    - 主键 `(id, sampled_at)`：表按 `sampled_at` 做 RANGE 分区，MySQL 要求分区列出现在所有唯一键中。
    """

    __tablename__ = "pod_metrics"
    __table_args__ = (
        UniqueConstraint("cluster_id", "namespace", "pod_name", "sampled_at",
                         name="uk_pod_metric_sample"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    namespace: Mapped[str] = mapped_column(String(100), index=True)
    pod_name: Mapped[str] = mapped_column(String(200), index=True)
    cpu_usage: Mapped[float] = mapped_column(Float, default=0.0)        # 单位：core
    memory_usage: Mapped[int] = mapped_column(BigInteger, default=0)    # 单位：byte
    network_rx: Mapped[int] = mapped_column(BigInteger, default=0)      # 单位：byte（累计）
    network_tx: Mapped[int] = mapped_column(BigInteger, default=0)      # 单位：byte（累计）
    sampled_at: Mapped[datetime] = mapped_column(
        DateTime, primary_key=True, index=True, server_default=func.now())
