# -*- coding: utf-8 -*-
"""Redis 连接与分布式锁工具（防多副本重复执行）"""
import redis as _redis_sync
import redis.asyncio as aioredis

from app.core.config import settings

# 全局 Redis 连接池
# 2026-11-26：Redis 端偶发 reset（192.168.6.99 空闲连接被服务端回收）会导致 auth 依赖直接 500。
# 加 keepalive / 健康检查 / 超时与重试，避免把「一次网络抖动」放大成整站 500。
redis_pool = aioredis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
    max_connections=50,
    socket_keepalive=True,
    socket_connect_timeout=3,
    socket_timeout=5,
    health_check_interval=30,
    retry_on_timeout=True,
)


async def get_redis() -> aioredis.Redis:
    """FastAPI 依赖：返回 Redis 客户端"""
    return redis_pool


# ------------------- 分布式锁 -------------------

# Lua 脚本：比对 value 后删除，防止误删其他副本的锁
_LUA_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


async def acquire_lock(
    redis: aioredis.Redis, lock_key: str, timeout_ms: int = 30000
) -> str | None:
    """获取分布式锁（SET NX PX），成功返回锁 value，失败返回 None"""
    value = __import__("app.core.security", fromlist=["generate_lock_value"]).generate_lock_value()
    ok = await redis.set(lock_key, value, nx=True, px=timeout_ms)
    return value if ok else None


async def release_lock(redis: aioredis.Redis, lock_key: str, value: str) -> None:
    """释放分布式锁（Lua 比对 value 后删除）"""
    await redis.eval(_LUA_RELEASE, 1, lock_key, value)


async def renew_lock(
    redis: aioredis.Redis, lock_key: str, value: str, timeout_ms: int = 30000
) -> bool:
    """续期锁（PEXPIRE），长任务定期调用；返回是否续期成功"""
    # 仅当 value 仍匹配时续期，防止已过期被他人持有时误续
    lua_renew = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('pexpire', KEYS[1], ARGV[2])
    else
        return 0
    end
    """
    return bool(await redis.eval(lua_renew, 1, lock_key, value, timeout_ms))


async def with_lock(
    redis: aioredis.Redis,
    lock_key: str,
    task,
    *args,
    timeout_ms: int = 30000,
    renew_interval_ms: int = 10000,
    **kwargs,
):
    """分布式锁执行上下文：拿锁 → 执行（自动续期）→ finally 释放

    用法：await with_lock(redis, "lock:custom_task:1", my_task, task_id=1)
    """
    import asyncio

    value = await acquire_lock(redis, lock_key, timeout_ms)
    if value is None:
        return None  # 未抢到锁，跳过（其他副本在执行）

    renew_task = None

    async def _renew_loop():
        while True:
            await asyncio.sleep(renew_interval_ms / 1000)
            if not await renew_lock(redis, lock_key, value, timeout_ms):
                break

    try:
        # 启动自动续期协程
        renew_task = asyncio.create_task(_renew_loop())
        return await task(*args, **kwargs)
    finally:
        if renew_task:
            renew_task.cancel()
        await release_lock(redis, lock_key, value)


# ------------------- 仪表盘缓存代次（实时失效） -------------------
# 背景：仪表盘聚合较重，走短 TTL 缓存 + 后台预热；但告警/工单/审批变更后
#       旧值要等 TTL(45s) 或预热(10s) 才更新，前端再轮询 15s → 最坏 ~25s 才反映。
# 方案：缓存键内嵌「代次」gen。任何告警/工单/审批写入 → gen+1（见 app/core/orm_hooks.py），
#       旧键立即不可达（无需 SCAN 删除，靠 TTL 自然回收），下次取数即重算 → 即时刷新。
_DASH_GEN_KEY = "cache:dashboard:gen"

# 同步 Redis 客户端：SQLAlchemy ORM 事件（非 async 上下文）里递增代次用
_sync_redis: "_redis_sync.Redis | None" = None


def get_sync_redis() -> "_redis_sync.Redis":
    """懒加载同步 Redis 客户端（仅供 ORM 事件等同步上下文使用）"""
    global _sync_redis
    if _sync_redis is None:
        _sync_redis = _redis_sync.Redis.from_url(
            settings.REDIS_URL, encoding="utf-8", decode_responses=True
        )
    return _sync_redis


def bump_dashboard_generation_sync() -> None:
    """同步递增仪表盘缓存代次（O(1)，供 SQLAlchemy ORM 事件调用；失败静默，退化为等 TTL）"""
    try:
        get_sync_redis().incr(_DASH_GEN_KEY)
    except Exception:  # noqa: BLE001
        pass


async def get_dashboard_generation() -> str:
    """读取代次（Redis 不可用时返回 "0"，不影响主流程）"""
    try:
        v = await redis_pool.get(_DASH_GEN_KEY)
        return v or "0"
    except Exception:  # noqa: BLE001
        return "0"


async def bump_dashboard_generation() -> None:
    """异步递增代次（供 async 业务代码显式失效用）"""
    try:
        await redis_pool.incr(_DASH_GEN_KEY)
    except Exception:  # noqa: BLE001
        pass
