# -*- coding: utf-8 -*-
"""值班排班模型：白班/夜班 × 工作日/周末/节假日 自由配置值班人"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OnDutySchedule(Base):
    """值班排班规则：适用日 × 班次 → 值班人"""

    __tablename__ = "on_duty_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    day_type: Mapped[str] = mapped_column(String(20), nullable=False)  # weekday/weekend/holiday
    shift_type: Mapped[str] = mapped_column(String(10), nullable=False)  # day/night
    start_time: Mapped[str] = mapped_column(String(5), default="09:00")  # HH:MM
    end_time: Mapped[str] = mapped_column(String(5), default="18:00")    # HH:MM（跨天如 18:00-09:00）
    user_ids: Mapped[str] = mapped_column(String(500), default="")  # 值班人 user_id 列表（逗号分隔）
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Holiday(Base):
    """节假日列表（管理员维护，用于值班路由判断）"""

    __tablename__ = "holidays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)  # YYYY-MM-DD
    name: Mapped[str] = mapped_column(String(100), default="")
