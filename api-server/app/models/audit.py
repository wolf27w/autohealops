# -*- coding: utf-8 -*-
"""审计模型：SHA256 哈希链防篡改"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuditRecord(Base):
    """审计记录（哈希链：hash_value = SHA256(previous_hash + 各字段拼接)）"""

    __tablename__ = "audit_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    trigger_snapshot: Mapped[str] = mapped_column(Text, default="")
    ai_call_detail: Mapped[str] = mapped_column(Text, default="")
    decision_detail: Mapped[str] = mapped_column(Text, default="")
    execution_detail: Mapped[str] = mapped_column(Text, default="")
    verification_detail: Mapped[str] = mapped_column(Text, default="")
    hash_value: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 hex
    previous_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
