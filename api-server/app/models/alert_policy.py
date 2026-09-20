# -*- coding: utf-8 -*-
"""告警策略模型：故障分类 + 观察期参数配置化"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AlertPolicy(Base):
    """告警策略：按 fault_type 配置分类（A/B/C）+ 观察期/重启阈值/时间窗口/severity"""

    __tablename__ = "alert_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fault_type: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(1), default="A")  # A=观察 B=直接报 C=自动处理
    observe_seconds: Mapped[int] = mapped_column(Integer, default=60)        # A 类观察期（秒）
    restart_threshold: Mapped[int] = mapped_column(Integer, default=2)       # 重启增长阈值（>=2 即重启>=3次）
    time_window_seconds: Mapped[int] = mapped_column(Integer, default=300)   # 判定窗口（秒，OOM 历史窗口）
    severity: Mapped[str] = mapped_column(String(10), default="high")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 通知渠道：关联 notification_configs（告警发该群并 @值班+审批）；未配置 → 只站内
    notify_config_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
