# -*- coding: utf-8 -*-
"""提示词模板模型：模板 + 版本管理"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PromptTemplate(Base):
    """提示词模板"""

    __tablename__ = "prompt_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)  # diagnosis/repair_plan/risk_assessment/verification
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[str] = mapped_column(String(500), default="[]")  # JSON 变量名列表
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # R13：自学习闭环——status(draft/active/archived)/source(manual/auto)/来源/演进/无效计数
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    source: Mapped[str] = mapped_column(String(20), default="manual", server_default="manual")
    source_refs: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    invalid_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # R10 收尾：auto 模板灰度比例（0~100；100 = 全量，不改变现有行为）
    gray_pct: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    # R10：场景（pod/node/workload/database/cluster/platform/action）+ 预览样例变量
    scene: Mapped[str] = mapped_column(String(30), default="", server_default="")
    sample_vars: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class PromptSuggestion(Base):
    """R13：未分类故障 → AI 起草的提示词建议（pending 待人工采纳，不自动建模板）"""

    __tablename__ = "prompt_suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fault_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    feature: Mapped[str] = mapped_column(String(200), default="", server_default="")
    hit_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    sample_logs: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggestion_name: Mapped[str] = mapped_column(String(100), default="", server_default="")
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_prompt_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    similarity_note: Mapped[str] = mapped_column(String(300), default="", server_default="")
    source_refs: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(50), default="system", server_default="system")


class PromptVersion(Base):
    """模板历史版本（更新时 version+1，旧版本存档）"""

    __tablename__ = "prompt_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    user_prompt_template: Mapped[str] = mapped_column(Text, default="")
    changelog: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
