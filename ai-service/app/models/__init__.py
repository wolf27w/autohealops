# -*- coding: utf-8 -*-
"""AI 服务模型（与 api-server 对应表字段一致；表结构由 api-server alembic 管理，本服务只读/写）"""
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SystemSetting(Base):
    """平台系统设置（key-value，与 api-server 对应表一致；embedding 配置读取用）"""

    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(String(200), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AIModelConfig(Base):
    __tablename__ = "ai_model_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    api_base: Mapped[str] = mapped_column(String(300), default="")
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # 默认模型（全局唯一，功能优先使用）
    cost_per_1k_tokens: Mapped[float] = mapped_column(Float, default=0.0)
    min_severity: Mapped[str] = mapped_column(String(10), default="low")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AICallLog(Base):
    __tablename__ = "ai_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_config_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    request_type: Mapped[str] = mapped_column(String(30), default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    user_prompt: Mapped[str] = mapped_column(Text, default="")
    response_content: Mapped[str] = mapped_column(Text, default="")
    token_usage: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="success")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[str] = mapped_column(String(500), default="[]")
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    source: Mapped[str] = mapped_column(String(20), default="manual", server_default="manual")
    source_refs: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    invalid_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # R10 收尾：auto 模板灰度比例（0~100；100 = 全量，不改变现有行为）
    gray_pct: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    # R10：场景 + 预览样例变量
    scene: Mapped[str] = mapped_column(String(30), default="", server_default="")
    sample_vars: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class PromptSuggestion(Base):
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
    __tablename__ = "prompt_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    user_prompt_template: Mapped[str] = mapped_column(Text, default="")
    changelog: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AIDiagnosisResult(Base):
    __tablename__ = "ai_diagnosis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    model_config_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    template_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_snapshot: Mapped[str] = mapped_column(Text, default="")
    diagnosis_json: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    is_useful: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RepairPlan(Base):
    __tablename__ = "repair_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    diagnosis_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_json: Mapped[str] = mapped_column(Text, nullable=False)
    operation_types: Mapped[str] = mapped_column(String(100), default="")
    risk_level: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RepairVerification(Base):
    __tablename__ = "repair_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    before_snapshot: Mapped[str] = mapped_column(Text, default="")
    after_snapshot: Mapped[str] = mapped_column(Text, default="")
    metrics_comparison: Mapped[str] = mapped_column(Text, default="")
    ai_verdict: Mapped[str] = mapped_column(String(30), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AIEffectivenessStat(Base):
    __tablename__ = "ai_effectiveness_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    model_config_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fault_type: Mapped[str] = mapped_column(String(50), default="")
    total_diagnoses: Mapped[int] = mapped_column(Integer, default=0)
    correct_diagnoses: Mapped[int] = mapped_column(Integer, default=0)
    total_repairs: Mapped[int] = mapped_column(Integer, default=0)
    successful_repairs: Mapped[int] = mapped_column(Integer, default=0)
    avg_mttr_seconds: Mapped[int] = mapped_column(Integer, default=0)
    total_cost: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ModelAccuracyStat(Base):
    __tablename__ = "model_accuracy_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_config_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    fault_type: Mapped[str] = mapped_column(String(50), default="")
    total_attempts: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    accuracy_rate: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fault_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    root_cause: Mapped[str] = mapped_column(Text, default="")
    repair_steps: Mapped[str] = mapped_column(Text, default="")
    execution_steps: Mapped[str] = mapped_column(Text, default="")  # 可执行修复步骤 JSON
    cluster_info: Mapped[str] = mapped_column(String(200), default="")
    tags: Mapped[str] = mapped_column(String(500), default="")
    # 分类：pod_crash（Pod 崩溃）/ db（数据库）/ application（应用）/ resource（资源）/ infra（基础设施）/ other
    category: Mapped[str] = mapped_column(String(20), default="other", index=True)
    # 诊断域（N3 三类分离）：cluster / app / db
    diag_category: Mapped[str] = mapped_column(String(10), default="app", index=True)
    # 条目来源：ai=AI 自动沉淀（默认锁定）/ manual=人工录入
    source: Mapped[str] = mapped_column(String(10), default="manual", index=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    vector_embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    pending_review: Mapped[bool] = mapped_column(Boolean, default=False)
    # 治理：失效停用（false 后不参与检索，与 api-server 模型保持一致）
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class PromptFragment(Base):
    """R10 公共片段（输出格式/红线/术语），模板用 `{{fragment:key}}` 引用"""
    __tablename__ = "prompt_fragments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), default="", server_default="")
    category: Mapped[str] = mapped_column(String(30), default="common", server_default="common")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class PromptRouting(Base):
    """R10 路由规则：fault_type/scene → 模板名（exact/prefix/default，priority 越小越优先）"""
    __tablename__ = "prompt_routing"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_type: Mapped[str] = mapped_column(String(20), default="fault_type", server_default="fault_type")
    match_value: Mapped[str] = mapped_column(String(100), default="", server_default="")
    match_mode: Mapped[str] = mapped_column(String(10), default="exact", server_default="exact")
    template_name: Mapped[str] = mapped_column(String(100), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
