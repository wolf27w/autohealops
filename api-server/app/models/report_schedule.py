# -*- coding: utf-8 -*-
"""报表定时推送配置（C4）：按 cron 定时把运行报表推送到指定 IM 通知渠道"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ReportSchedule(Base):
    """报表定时推送任务"""

    __tablename__ = "report_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 5 段 cron（分 时 日 月 周），如 "0 9 * * *" = 每天 09:00
    cron_expression: Mapped[str] = mapped_column(String(50), nullable=False)
    # 推送目标：notification_configs.id（钉钉/企微/飞书渠道）
    notify_config_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 报表统计窗口（天）
    range_days: Mapped[int] = mapped_column(Integer, default=7)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
