# -*- coding: utf-8 -*-
"""定时任务模型：每集群独立 Cron + 执行日志"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CustomTask(Base):
    """定制化定时任务（按集群配置 Cron 表达式）"""

    __tablename__ = "custom_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    task_type: Mapped[str] = mapped_column(String(20), nullable=False)  # restart/scale/inspect/script/once
    target_resource: Mapped[str] = mapped_column(String(200), default="")
    target_namespace: Mapped[str] = mapped_column(String(100), default="")
    cron_expression: Mapped[str] = mapped_column(String(50), nullable=False)
    script_content: Mapped[str] = mapped_column(Text, default="")
    parameters: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_status: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class CustomTaskLog(Base):
    """定时任务执行日志"""

    __tablename__ = "custom_task_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    cluster_id: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running/success/failed
    output: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
