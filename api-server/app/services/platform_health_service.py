# -*- coding: utf-8 -*-
"""平台自身依赖探活（去重：/ready 与 /stats/platform-health 原本各查一遍 DB/Redis）"""
from __future__ import annotations

from sqlalchemy import text

from app.core.database import async_session
from app.core.redis_client import redis_pool


async def check_dependencies() -> dict:
    """探活数据库与 Redis，返回 {api_server, database, redis}（api_server 恒 True，能执行到即存活）"""
    db_ok = True
    redis_ok = True
    try:
        async with async_session() as s:
            await s.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    try:
        await redis_pool.ping()
    except Exception:  # noqa: BLE001
        redis_ok = False
    return {"api_server": True, "database": db_ok, "redis": redis_ok}
