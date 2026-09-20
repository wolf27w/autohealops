# -*- coding: utf-8 -*-
"""容器资源阈值告警规则模型（Prometheus 数据源）"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ResourceAlertRule(Base):
    """容器资源阈值规则：CPU/内存使用率超阈值持续 N 秒 → 告警

    - namespace_match：空=全部；支持前缀通配（如 prod-* / release-*）
    - metric_type：cpu / memory（扩展预留：redis_conn/mq_backlog 等中间件指标）
    - 无 limit 的容器由 PromQL join 不到 limit 自然跳过
    """

    __tablename__ = "resource_alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    cluster_id: Mapped[int] = mapped_column(Integer, default=0, index=True)  # 0=全部集群
    namespace_match: Mapped[str] = mapped_column(String(100), default="")    # 空=全部；prod-* 前缀通配
    container_match: Mapped[str] = mapped_column(String(100), default="")    # 空=全部容器
    metric_type: Mapped[str] = mapped_column(String(20), default="memory")   # cpu/memory
    threshold_pct: Mapped[float] = mapped_column(Float, default=80.0)        # 使用率阈值（%）
    duration_seconds: Mapped[int] = mapped_column(Integer, default=300)      # 持续超限多久才告警（防抖）
    severity: Mapped[str] = mapped_column(String(10), default="medium")      # high/medium（告警级别）
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
