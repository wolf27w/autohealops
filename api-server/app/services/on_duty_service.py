# -*- coding: utf-8 -*-
"""值班路由服务：按当前时间（工作日/周末 × 白班/夜班）解析值班人"""
from datetime import datetime

from sqlalchemy import select

from app.core.database import async_session
from app.models import OnDutySchedule, User


def _in_time_range(now_hm: str, start: str, end: str) -> bool:
    """判断时间是否在 [start, end) 区间内（左闭右开，支持跨天）

    左闭右开避免相邻班次在边界同时命中（如 白班 09:00-18:00 与 夜班 18:00-09:00：
    18:00 只算夜班），否则 resolver 取第一个命中项会导致晚班在 18:00 拿不到通知。
    """
    if not start or not end:
        return False
    if start == end:
        return False  # 无有效区间（等同不配）
    if start < end:
        return start <= now_hm < end
    # 跨天（如 18:00-09:00）
    return now_hm >= start or now_hm < end


async def resolve_duty_users(now: datetime | None = None) -> list[int]:
    """按当前时间解析值班人 user_id 列表（工作日/周末，不再依赖节假日）"""
    now = now or datetime.now()
    day_type = "weekend" if now.weekday() >= 5 else "weekday"

    now_hm = now.strftime("%H:%M")
    async with async_session() as session:
        result = await session.execute(
            select(OnDutySchedule).where(
                OnDutySchedule.day_type == day_type,
                OnDutySchedule.enabled.is_(True),
            )
        )
        schedules = list(result.scalars())

    for s in schedules:
        if _in_time_range(now_hm, s.start_time, s.end_time):
            return [int(x) for x in s.user_ids.split(",") if x.strip().isdigit()]
    return []


async def resolve_duty_user_objects(now: datetime | None = None) -> list[User]:
    """解析值班人 User 对象列表"""
    ids = await resolve_duty_users(now)
    if not ids:
        return []
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id.in_(ids)))
        return list(result.scalars())
