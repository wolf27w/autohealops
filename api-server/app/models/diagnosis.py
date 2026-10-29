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
    # 1=有用 / -1=无用 / NULL=未评价（tinyint(1) 实际能存 -1；用 Integer 映射避免 ORM 布尔校验拦截）
    is_useful: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DiagnosisFailure(Base):
    """诊断失败记录（老板 2026-10-29 口径：诊断失败要单独记录）

    原来 AI 诊断失败只把告警标成「需人工协助」，没有结构化留痕 →
    失败率、失败原因分布、哪些故障永远诊断不出来，全都无从统计。
    """
    __tablename__ = "diagnosis_failures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False, default=0)
    cluster_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    namespace: Mapped[str] = mapped_column(Text, default="")
    resource_type: Mapped[str] = mapped_column(Text, default="")
    resource_name: Mapped[str] = mapped_column(Text, default="")
    fault_type: Mapped[str] = mapped_column(Text, default="", index=True)
    # 失败阶段：diagnose=AI 诊断 / plan=方案生成 / manual=人工触发诊断
    stage: Mapped[str] = mapped_column(Text, default="diagnose", index=True)
    # 失败分类：ai_error=模型/网络异常 / empty_result=模型返回无结论 / plan_error=方案生成失败
    reason: Mapped[str] = mapped_column(Text, default="ai_error", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
