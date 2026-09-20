# -*- coding: utf-8 -*-
"""值班换班/代班（C6）：某一天的临时替换（覆盖排班规则）"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DutyOverride(Base):
    """值班换班/代班：某天把 original 换成 substitute（type: swap=换班 / substitute=代班）"""

    __tablename__ = "duty_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), index=True, nullable=False)  # YYYY-MM-DD
    original_username: Mapped[str] = mapped_column(String(50), default="")
    substitute_username: Mapped[str] = mapped_column(String(50), nullable=False)
    type: Mapped[str] = mapped_column(String(10), default="swap")  # swap/substitute
    note: Mapped[str] = mapped_column(String(255), default="")
    created_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
