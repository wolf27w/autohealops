# -*- coding: utf-8 -*-
"""自愈场景规则模型：按「集群/命名空间/故障类型/资源类型」配置强制人工或强制自动

用于覆盖默认风险评分决策（执行决策矩阵的「策略层规则库」），支持影子模式（只记录不生效）。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SelfHealRule(Base):
    """自愈场景规则：匹配条件 → 决策覆盖（force_manual/force_auto）"""

    __tablename__ = "self_heal_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 匹配条件（空 = 不限制该维度）
    cluster_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    namespace_match: Mapped[str] = mapped_column(String(100), default="")   # 支持 prod-* 前缀通配
    fault_type_match: Mapped[str] = mapped_column(String(100), default="")
    resource_type_match: Mapped[str] = mapped_column(String(50), default="")  # pod/node 等
    # 决策覆盖
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # force_manual=强制人工 / force_auto=强制自动
    priority: Mapped[int] = mapped_column(Integer, default=100)       # 数字小=优先级高
    shadow: Mapped[bool] = mapped_column(Boolean, default=True)       # 影子模式：只记录不生效，人工确认后关闭
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
