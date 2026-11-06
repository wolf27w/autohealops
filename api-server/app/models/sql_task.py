# -*- coding: utf-8 -*-
"""SQL 定时任务：周期性执行一段 SQL，把结果（CSV 附件）发到指定邮箱。

场景（老板 2026-11-06）：每月执行一次，把最近一个月的数据发给某位邮箱用户。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SqlTask(Base):
    __tablename__ = "db_sql_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    db_connection_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    # 5 段 cron：分 时 日 月 周（每月 1 号 9 点 = 0 9 1 * *）
    cron_expression: Mapped[str] = mapped_column(String(100), default="")
    # 收件人邮箱，逗号分隔（也支持平台用户邮箱）
    recipients: Mapped[str] = mapped_column(Text, default="")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[str] = mapped_column(String(20), default="")   # success / failed
    last_error: Mapped[str] = mapped_column(String(500), default="")
    created_by: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
