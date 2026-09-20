# -*- coding: utf-8 -*-
"""AI 诊断结果模型"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AIDiagnosisResult(Base):
    """AI 诊断结果"""

    __tablename__ = "ai_diagnosis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    model_config_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    template_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_snapshot: Mapped[str] = mapped_column(Text, default="")  # 诊断上下文快照（含指标/日志）
    diagnosis_json: Mapped[str] = mapped_column(Text, default="")  # 诊断结果 JSON
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    is_useful: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # 用户反馈是否有用
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
