# -*- coding: utf-8 -*-
"""AI 反馈闭环（C5）：对 AI 诊断/修复结果打分（有用/无用 + 备注）"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AiFeedback(Base):
    """AI 结果反馈（rating: 1=有用 / -1=无用）"""

    __tablename__ = "ai_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    diagnosis_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[int] = mapped_column(Integer, default=0)  # 1 / -1
    comment: Mapped[str] = mapped_column(String(500), default="")
    username: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
