# -*- coding: utf-8 -*-
"""executor 存活探测与掉线告警（M2：executor 高可用——平台侧检测）

executor（k8s-executor）是平台的核心单实例（gRPC 9090 + 日志流 HTTP 9099，同进程）。
本服务周期探测其可达性，**状态翻转时通知**（掉线→告警，恢复→告知），避免反复刷屏。

探活手段：
- logstream HTTP `GET {EXECUTOR_LOGSTREAM_ADDR}/healthz`（200 = 进程在）
- gRPC 地址 TCP 可达（{K8S_EXECUTOR_GRPC_ADDR}）

（探针/自动重启由 k8s liveness 负责；本服务补的是「平台能不能感知到 executor 掉了」。）
"""
import asyncio

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis_client import redis_pool

logger = get_logger("api-server.executor-health")

_STATE_KEY = "platform:executor_state"   # "up" / "down"
PROBE_INTERVAL_SEC = 60


async def probe_executor(timeout: float = 3.0) -> dict:
    """探测 executor 进程存活。返回 {running, logstream, grpc, detail}。"""
    base = getattr(settings, "EXECUTOR_LOGSTREAM_ADDR", "http://localhost:9099")
    grpc = getattr(settings, "K8S_EXECUTOR_GRPC_ADDR", "localhost:9090")
    http_ok = False
    grpc_ok = False
    detail: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as c:
            r = await c.get(f"{base.rstrip('/')}/healthz")
            http_ok = r.status_code == 200
    except Exception as e:  # noqa: BLE001
        detail.append(f"logstream: {e}")
    try:
        host, _, port = grpc.rpartition(":")
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host or "localhost", int(port or "9090")), timeout
        )
        grpc_ok = True
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        detail.append(f"grpc: {e}")
    return {"running": http_ok or grpc_ok, "logstream": http_ok, "grpc": grpc_ok, "detail": " ; ".join(detail)}


def evaluate_transition(prev: str | None, running: bool) -> tuple[str, str]:
    """根据上次状态与本次探测，返回 (动作, 新状态)。

    动作：'down'（本次掉线，需告警）/ 'up'（本次恢复，需告知）/ ''（无变化）。
    """
    state = "up" if running else "down"
    if running and prev == "down":
        return "up", state
    if (not running) and prev != "down":
        return "down", state
    return "", state


async def _notify(content: str) -> None:
    try:
        from app.services.notification_service import notify_platform
        await notify_platform(content, at_all=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("平台事件通知失败: %s", e)


async def check_once() -> dict:
    """探测一次，并在状态翻转时通知。返回 probe 结果 + 动作。"""
    st = await probe_executor()
    try:
        prev = await redis_pool.get(_STATE_KEY)
    except Exception:  # noqa: BLE001
        prev = None
    action, new_state = evaluate_transition(prev, st["running"])
    if action:
        if action == "down":
            await _notify(f"⚠️ 平台组件异常：k8s-executor 不可达（{st['detail'][:200]}）。"
                          f"平台自愈/执行链路将受影响，请检查 executor 进程与部署（liveness 应会重启）。")
        else:
            await _notify("✅ 平台组件恢复：k8s-executor 已恢复可达。")
        try:
            await redis_pool.set(_STATE_KEY, new_state)
        except Exception:  # noqa: BLE001
            pass
        logger.info("executor 状态翻转：%s → %s（%s）", prev, new_state, action)
    return {**st, "action": action, "state": new_state}


async def background_loop() -> None:
    while True:
        try:
            await check_once()
        except Exception as e:  # noqa: BLE001
            logger.warning("executor 探活异常: %s", e)
        await asyncio.sleep(PROBE_INTERVAL_SEC)
