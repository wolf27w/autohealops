# -*- coding: utf-8 -*-
"""站内消息模型：右上角铃铛消息中心"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class InAppMessage(Base):
    """站内消息（未读计数 + 列表 + 已读）"""

    __tablename__ = "in_app_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(20), default="info")  # alert/approval/info
    link: Mapped[str] = mapped_column(String(500), default="")  # 跳转链接（可选）
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
