# -*- coding: utf-8 -*-
"""告警屏蔽（R11，Alertmanager 风格）：命中匹配条件时只拦通知、不丢数据。

matchers 为 JSON 数组：[{key, op, value}]，op ∈ = != =~ !~（正则全匹配）。
所有 matcher 都命中（AND）→ 屏蔽生效。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AlertSilence(Base):
    __tablename__ = "alert_silences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    matchers: Mapped[str] = mapped_column(Text, default="[]")   # JSON: [{key,op,value}]
    recurrence: Mapped[str] = mapped_column(Text, default="{}")  # JSON: {"type":"daily|weekly|once","days":[...],"start":"HH:MM","end":"HH:MM"}
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    comment: Mapped[str] = mapped_column(String(255), default="")
    created_by: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AlertInhibition(Base):
    """告警抑制规则（R11，Alertmanager 风格）：source 告警命中时抑制 target 告警通知。

    source_matchers / target_matchers 均为 JSON：[{key,op,value}]（复用 silence_matches 语义）。
    equal_labels 为 JSON 字符串数组：抑制需 source/target 在这些标签上取值相等（如 namespace）。
    """

    __tablename__ = "alert_inhibitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    source_matchers: Mapped[str] = mapped_column(Text, default="[]")
    target_matchers: Mapped[str] = mapped_column(Text, default="[]")
    equal_labels: Mapped[str] = mapped_column(Text, default="[]")   # JSON: ["namespace","cluster_id",...]
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    comment: Mapped[str] = mapped_column(String(255), default="")
    created_by: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
