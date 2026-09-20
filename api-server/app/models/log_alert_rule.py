# -*- coding: utf-8 -*-
"""日志告警规则模型（ElastAlert 模式：索引 + 关键词 + 窗口 + 阈值）"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LogAlertRule(Base):
    """日志关键词告警规则"""

    __tablename__ = "log_alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    cluster_id: Mapped[int] = mapped_column(Integer, default=0)  # 绑定集群（0=全局）
    index_pattern: Mapped[str] = mapped_column(String(200), nullable=False)  # ES 索引，如 app-* / nginx-*
    keywords: Mapped[str] = mapped_column(Text, nullable=False)  # JSON 数组，命中任一计数
    namespace_filter: Mapped[str] = mapped_column(String(100), default="")  # 可选限定 namespace
    time_window: Mapped[int] = mapped_column(Integer, default=5)   # 统计窗口（分钟）
    threshold: Mapped[int] = mapped_column(Integer, default=3)     # 窗口内命中次数阈值
    severity: Mapped[str] = mapped_column(String(10), default="medium")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
