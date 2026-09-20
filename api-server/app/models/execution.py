# -*- coding: utf-8 -*-
"""执行日志模型：修复步骤执行记录（前端轮询数据源）"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ExecutionLog(Base):
    """修复执行步骤日志（实时写入，前端按 since_step 增量轮询）"""

    __tablename__ = "execution_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running/success/failed/rolled_back
    output: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
