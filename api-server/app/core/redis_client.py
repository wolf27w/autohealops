# -*- coding: utf-8 -*-
"""Redis 连接与分布式锁工具（防多副本重复执行）"""
import redis.asyncio as aioredis

from app.core.config import settings

# 全局 Redis 连接池
redis_pool = aioredis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
    max_connections=50,
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
