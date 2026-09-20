# -*- coding: utf-8 -*-
"""统计模型：AI 有效性统计 + 模型准确率统计"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AIEffectivenessStat(Base):
    """按日聚合的 AI 有效性统计"""

    __tablename__ = "ai_effectiveness_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    model_config_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fault_type: Mapped[str] = mapped_column(String(50), default="")
    total_diagnoses: Mapped[int] = mapped_column(Integer, default=0)
    correct_diagnoses: Mapped[int] = mapped_column(Integer, default=0)
    total_repairs: Mapped[int] = mapped_column(Integer, default=0)
    successful_repairs: Mapped[int] = mapped_column(Integer, default=0)
    avg_mttr_seconds: Mapped[int] = mapped_column(Integer, default=0)  # 平均修复时长（秒）
    total_cost: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ModelAccuracyStat(Base):
    """模型准确率统计（多模型仲裁用）"""

    __tablename__ = "model_accuracy_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_config_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    fault_type: Mapped[str] = mapped_column(String(50), default="")
    total_attempts: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    accuracy_rate: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
