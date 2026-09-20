# -*- coding: utf-8 -*-
"""任务模型：告警处理任务 + 状态流转审计日志"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Task(Base):
    """处理任务（与告警关联，状态机：pending→acknowledged→in_progress→resolved）"""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    assignee: Mapped[str] = mapped_column(String(50), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    resolution: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class TaskAuditLog(Base):
    """任务状态变更时间线"""

    __tablename__ = "task_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)  # acknowledge/start/resolve
    old_status: Mapped[str] = mapped_column(String(20), default="")
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)
    operator: Mapped[str] = mapped_column(String(50), default="")
    comment: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
