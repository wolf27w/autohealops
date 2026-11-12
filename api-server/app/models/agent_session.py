# -*- coding: utf-8 -*-
"""AI 诊断助手（会话式 Agent）模型：会话 + 消息"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DiagnosisSession(Base):
    """诊断会话（按 user_id 隔离，绑定集群/namespace/pod）"""

    __tablename__ = "diagnosis_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    cluster_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    namespace: Mapped[str] = mapped_column(String(100), default="")
    pod_name: Mapped[str] = mapped_column(String(200), default="")
    # 会话名规范（老板 2026-10-16）：环境/命名空间/服务[容器]；同目标不重复建会话
    service: Mapped[str] = mapped_column(String(200), default="")
    container: Mapped[str] = mapped_column(String(200), default="")
    title: Mapped[str] = mapped_column(String(200), default="")  # 会话标题（首条消息摘要）
    # 来源：agent=用户在诊断助手主动发起 / proactive=平台巡检自动发起（异常但未告警）/ alert=从告警一键转来
    source: Mapped[str] = mapped_column(String(20), default="agent", index=True)
    # 类别（2026-11-11 老板口径）：app=应用 / node=节点 / db=数据库 / cluster=集群；
    # 会话标题带类别前缀，让「应用诊断助手」里的条目一眼能分清是哪类问题
    category: Mapped[str] = mapped_column(String(20), default="app", server_default="app", index=True)
    # 由告警转来时记下告警 id（闭环：会话 ↔ 告警互相可跳转）
    alert_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # active=进行中 / closed=已闭环（处理完成，落盘知识库）
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    # 会话共享（2026-11-12）：shared=True 表示共享到本 namespace（有该 namespace 权限的同事可见）
    shared: Mapped[bool] = mapped_column(Boolean, default=False)
    shared_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)  # 共享时间（取消共享时置空）
    shared_by: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 操作共享的用户 id（取消共享时置空）
    # 定时采集（针对该会话目标容器周期采集信息，追加到会话）
    collect_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    collect_interval_min: Mapped[int] = mapped_column(Integer, default=5)
    collect_items: Mapped[str] = mapped_column(String(100), default="jvm")  # CSV: jvm,logs,...
    collect_last_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class DiagnosisMessage(Base):
    """诊断会话消息（多轮上下文 + 工具调用留痕）"""

    __tablename__ = "diagnosis_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    # role: user=用户输入 / assistant=AI 回复（含诊断结论）/ tool=工具调用记录（审计留痕）
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")  # 文本内容
    tool_name: Mapped[str] = mapped_column(String(50), default="")  # tool 消息：工具名
    tool_args: Mapped[str] = mapped_column(Text, default="")  # tool 消息：工具参数 JSON
    tool_output: Mapped[str] = mapped_column(Text, default="")  # tool 消息：工具输出（截断）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
