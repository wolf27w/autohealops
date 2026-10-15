# -*- coding: utf-8 -*-
"""监控数据源（按集群覆盖 Prometheus 地址）

默认：集群自带 Prometheus 由 executor 自动发现（apiserver Service Proxy）。
本表：为某集群显式指定 Prometheus 地址时，api-server 直接查询该地址（不依赖集群内发现）。

解析优先级（见 prometheus_service.resolve_source）：
    按集群覆盖（本表） > 全局设置 prometheus_url > executor 自动发现
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MonitorSource(Base):
    __tablename__ = "monitor_sources"

    cluster_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(255), default="")  # 空 = 不用（回退全局/自动发现）
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
