# -*- coding: utf-8 -*-
"""修复方案模型：AI 生成的修复计划"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RepairPlan(Base):
    """修复方案（plan_json 含 steps 数组，每步含 command/description/expected_result/rollback_command）"""

    __tablename__ = "repair_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    diagnosis_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_json: Mapped[str] = mapped_column(Text, nullable=False)
    operation_types: Mapped[str] = mapped_column(String(100), default="")  # 逗号分隔 READ/CREATE/UPDATE/DELETE
    risk_level: Mapped[str] = mapped_column(String(20), default="medium")  # low/medium/high/critical
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending/executing/completed/failed
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
