# -*- coding: utf-8 -*-
"""平台自身依赖探活（去重：/ready 与 /stats/platform-health 原本各查一遍 DB/Redis）"""
from __future__ import annotations

from sqlalchemy import text

from app.core.database import async_session
from app.core.redis_client import redis_pool


async def check_dependencies(with_executor: bool = False) -> dict:
    """探活数据库与 Redis（+可选 executor），返回 {api_server, database, redis[, executor]}。

    /ready 探针用（with_executor=False，保持快速）；/stats/platform-health 用（True）多看 executor。
    """
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
    out = {"api_server": True, "database": db_ok, "redis": redis_ok}
    if with_executor:
        try:
            from app.services.executor_health_service import probe_executor
            st = await probe_executor()
            out["executor"] = st["running"]
            out["executor_detail"] = {"logstream": st["logstream"], "grpc": st["grpc"], "error": st["detail"]}
        except Exception as e:  # noqa: BLE001
            out["executor"] = False
            out["executor_detail"] = {"error": str(e)}
    return out
