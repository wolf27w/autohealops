# -*- coding: utf-8 -*-
"""AI 模型模型：模型配置（severity 分级）+ 调用日志"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AIModelConfig(Base):
    """AI 模型配置（min_severity 实现 severity 分级选模型）"""

    __tablename__ = "ai_model_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)  # openai/deepseek/claude
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    api_base: Mapped[str] = mapped_column(String(300), default="")
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    priority: Mapped[int] = mapped_column(Integer, default=100)  # 越小越优先（降级链顺序）
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    cost_per_1k_tokens: Mapped[float] = mapped_column(Float, default=0.0)
    # 该模型可服务的最低告警级别（critical/high/medium/low）：critical 告警用贵模型，medium/low 用便宜模型
    min_severity: Mapped[str] = mapped_column(String(10), default="low")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AICallLog(Base):
    """AI 调用日志（token 消耗 + 成本核算）"""

    __tablename__ = "ai_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_config_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    request_type: Mapped[str] = mapped_column(String(30), default="")  # diagnosis/repair_plan/verification...
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    user_prompt: Mapped[str] = mapped_column(Text, default="")
    response_content: Mapped[str] = mapped_column(Text, default="")
    token_usage: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="success")  # success/failed
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
