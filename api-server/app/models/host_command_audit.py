# -*- coding: utf-8 -*-
"""B8 主机终端命令审计 + 危险命令拦截。

- 每次用户按回车提交的「整行命令」落一条审计记录：
  - action=blocked：命中危险规则，被拦截（未转发给 SSH）
  - action=allowed：未命中，正常转发执行（risk=low）
- session_id 关联 host_sessions.id（可空，非强制外键，便于会话元信息独立清理）
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HostCommandAudit(Base):
    __tablename__ = "host_command_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    host_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    host_name: Mapped[str] = mapped_column(String(128), default="", server_default="")
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(64), default="", server_default="")
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    risk: Mapped[str] = mapped_column(String(16), default="low", server_default="low")  # low/medium/high
    action: Mapped[str] = mapped_column(String(16), default="allowed", server_default="allowed")  # allowed/blocked
    reason: Mapped[str] = mapped_column(String(255), default="", server_default="")
    client_ip: Mapped[str] = mapped_column(String(64), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
