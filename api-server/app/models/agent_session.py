# -*- coding: utf-8 -*-
"""AI 诊断助手（会话式 Agent）模型：会话 + 消息"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
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
    title: Mapped[str] = mapped_column(String(200), default="")  # 会话标题（首条消息摘要）
    # 来源：agent=用户在诊断助手主动发起 / proactive=平台巡检自动发起（异常但未告警）
    source: Mapped[str] = mapped_column(String(20), default="agent", index=True)
    # active=进行中 / closed=已闭环（处理完成，落盘知识库）
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
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
