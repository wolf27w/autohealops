# -*- coding: utf-8 -*-
"""修复验证模型：修复前后快照对比 + AI 判定"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RepairVerification(Base):
    """修复验证记录（AI verdict：healed/degraded/unchanged/worsened）"""

    __tablename__ = "repair_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    before_snapshot: Mapped[str] = mapped_column(Text, default="")
    after_snapshot: Mapped[str] = mapped_column(Text, default="")
    metrics_comparison: Mapped[str] = mapped_column(Text, default="")
    ai_verdict: Mapped[str] = mapped_column(String(30), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/success/failed
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
