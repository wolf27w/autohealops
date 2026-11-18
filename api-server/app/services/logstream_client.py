# -*- coding: utf-8 -*-
"""执行器日志流 HTTP 客户端（按时间窗口 / 整个文件取日志）。

为什么不走 gRPC：`GetPodLogs` 只有 tail（行数）概念，做不到「最近 2 分钟」；
executor 的 9099 日志流服务直接透传 apiserver `PodLogOptions`，支持
`since_seconds` / `all`（不截断），所以日志快照/下载走这里。

注意：
- 该 HTTP 服务绑定在**平台侧 executor**；agent 接入的集群平台没有 kubeconfig →
  请求会 502，调用方需回落到 gRPC tail（行数近似）。返回值 error 非空即表示可回落。
- 日志流固定 `Timestamps: true`，本模块统一剥掉行首 RFC3339 时间戳，与 gRPC 口径一致。
"""
import re

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("api-server.logstream")

# apiserver 时间戳前缀：2026-11-18T11:04:05.106864905+08:00␠
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\s?")


def _base() -> str:
    return (getattr(settings, "EXECUTOR_LOGSTREAM_ADDR", "http://localhost:9099") or "").rstrip("/")


def strip_timestamps(text: str) -> str:
    """剥掉每行行首的 apiserver 时间戳（日志流固定带时间戳）。"""
    if not text:
        return ""
    return "\n".join(_TS_RE.sub("", line) for line in text.splitlines()) + ("\n" if text.endswith("\n") else "")


async def fetch_logs(cluster_id: int, namespace: str, pod: str, container: str = "",
                     since_seconds: int = 0, all_lines: bool = False, previous: bool = False,
                     tail: int = 0, timeout: float = 30.0) -> tuple[str, str]:
    """取一次日志（follow=false）。返回 (text, error)。

    - since_seconds > 0：只取最近 N 秒（优先）
    - all_lines=True：不截断（整个日志文件）
    - 否则按 tail 行数
    error 非空 = 失败（调用方回落 gRPC 并降级为行数口径）。
    """
    params: dict = {
        "cluster_id": int(cluster_id), "namespace": namespace, "pod": pod,
        "follow": "false", "container": container or "",
        "previous": "true" if previous else "false",
    }
    if since_seconds and int(since_seconds) > 0:
        params["since_seconds"] = int(since_seconds)
    elif all_lines:
        params["all"] = "true"
    elif tail and int(tail) > 0:
        params["tail"] = int(tail)
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            resp = await client.get(f"{_base()}/logs", params=params)
            if resp.status_code != 200:
                return "", f"{resp.status_code} {resp.text[:200]}"
            return strip_timestamps(resp.text), ""
    except Exception as e:  # noqa: BLE001
        logger.debug("日志流请求失败 cluster=%s %s/%s: %s", cluster_id, namespace, pod, e)
        return "", f"日志流不可用: {e}"
