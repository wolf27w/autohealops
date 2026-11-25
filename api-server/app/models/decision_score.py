# -*- coding: utf-8 -*-
"""修复决策评分留痕（R77，2026-11-25）。

shadow 模式只写这张表不改决策；积累样本后用数据定阈值，再切 enforce。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DecisionScore(Base):
    """一次「要不要自动修」的加权评分结果（可解释、可回溯）"""

    __tablename__ = "decision_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    plan_id: Mapped[int] = mapped_column(Integer, default=0)
    trace_id: Mapped[str] = mapped_column(String(64), default="")
    score: Mapped[int] = mapped_column(Integer, default=0)
    recommend: Mapped[str] = mapped_column(String(20), default="")   # auto/approval_1/manual
    # 本次实际执行的决策（shadow 模式下用于对比评分建议）
    final_action: Mapped[str] = mapped_column(String(30), default="")
    mode: Mapped[str] = mapped_column(String(10), default="shadow")   # shadow/enforce
    hard_blocks: Mapped[str] = mapped_column(Text, default="")        # 命中的硬红线（；分隔）
    factors_json: Mapped[str] = mapped_column(Text, default="")       # [{name,delta,reason}]
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
