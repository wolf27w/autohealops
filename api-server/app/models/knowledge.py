# -*- coding: utf-8 -*-
"""知识库模型：向量嵌入（384 维）+ 修复案例统计"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class KnowledgeEntry(Base):
    """故障知识条目（向量嵌入存 LargeBinary，384 维 float32 = 1536 字节）"""

    __tablename__ = "knowledge_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fault_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    root_cause: Mapped[str] = mapped_column(Text, default="")
    repair_steps: Mapped[str] = mapped_column(Text, default="")
    # 可执行修复步骤（JSON 字符串，如 [{"command":"kubectl delete pod ...","description":"..."}]）
    # 命中后直接复用执行，实现「知识库命中零 AI」
    execution_steps: Mapped[str] = mapped_column(Text, default="")
    cluster_info: Mapped[str] = mapped_column(String(200), default="")
    tags: Mapped[str] = mapped_column(String(500), default="")
    # 分类：pod_crash（Pod 崩溃）/ db（数据库）/ application（应用）/ resource（资源）/ infra（基础设施）/ other
    category: Mapped[str] = mapped_column(String(20), default="other", index=True)
    # 条目来源：ai=AI 自动沉淀 / manual=人工录入；AI 沉淀默认锁定（编辑需 admin 解锁）
    source: Mapped[str] = mapped_column(String(10), default="manual", index=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    # 384 维 float32 向量字节（np.float32 序列化）
    vector_embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # 修复方案与知识库建议冲突时标记人工 review
    pending_review: Mapped[bool] = mapped_column(Boolean, default=False)
    # 治理：失效停用（false 后不再参与检索/复用）
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
