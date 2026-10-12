# -*- coding: utf-8 -*-
"""告警屏蔽（R11，Alertmanager 风格）：命中匹配条件时只拦通知、不丢数据。

matchers 为 JSON 数组：[{key, op, value}]，op ∈ = != =~ !~（正则全匹配）。
所有 matcher 都命中（AND）→ 屏蔽生效。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AlertSilence(Base):
    __tablename__ = "alert_silences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    matchers: Mapped[str] = mapped_column(Text, default="[]")   # JSON: [{key,op,value}]
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    comment: Mapped[str] = mapped_column(String(255), default="")
    created_by: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
