# -*- coding: utf-8 -*-
"""决策模型：时间策略 + 决策结果"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DecisionPolicy(Base):
    """决策策略（按时间段配置操作规则）"""

    __tablename__ = "decision_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(20), nullable=False)  # working_hours/off_hours/weekend/holiday
    days_of_week: Mapped[str] = mapped_column(String(20), default="1,2,3,4,5")
    start_time: Mapped[str] = mapped_column(String(5), default="09:00")
    end_time: Mapped[str] = mapped_column(String(5), default="18:00")
    read_rule: Mapped[str] = mapped_column(String(30), default="auto")
    create_rule: Mapped[str] = mapped_column(String(30), default="approval_1")
    update_rule: Mapped[str] = mapped_column(String(30), default="approval_1")
    delete_rule: Mapped[str] = mapped_column(String(30), default="approval_2")
    auto_escalation: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class DecisionResult(Base):
    """计划决策结果（操作类型 × 时间策略 双维度评估）"""

    __tablename__ = "decision_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(20), default="")
    overall_operation_type: Mapped[str] = mapped_column(String(20), default="")
    overall_decision: Mapped[str] = mapped_column(String(30), default="")
    read_ops_count: Mapped[int] = mapped_column(Integer, default=0)
    create_ops_count: Mapped[int] = mapped_column(Integer, default=0)
    update_ops_count: Mapped[int] = mapped_column(Integer, default=0)
    delete_ops_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
