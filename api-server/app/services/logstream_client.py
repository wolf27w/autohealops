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
from datetime import datetime, timezone

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


def _line_epoch(line: str) -> float | None:
    """解析行首 RFC3339 时间戳为 epoch 秒；无时间戳返回 None。"""
    m = _TS_RE.match(line)
    if not m:
        return None
    ts_str = m.group(0).strip()
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _parse_time(v) -> float | None:
    """解析 start/end 参数：优先按 epoch 秒（纯数字），否则按 RFC3339/ISO 字符串。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def filter_logs_by_time(text: str, start, end) -> str:
    """按 [start, end]（epoch 秒或 RFC3339）过滤带时间戳的日志行，返回剥掉时间戳的文本。

    - 无时间戳的续行跟随上一行（上一行保留则续行一并保留）。
    - start/end 无法解析时不过滤，仅剥时间戳（等价于 strip_timestamps）。
    """
    if not text:
        return ""
    lo = _parse_time(start)
    hi = _parse_time(end)
    if lo is None or hi is None:
        return strip_timestamps(text)
    if lo > hi:
        lo, hi = hi, lo
    keep: list[str] = []
    last_kept = False
    for line in text.splitlines():
        ts = _line_epoch(line)
        if ts is None:
            if last_kept:
                keep.append(line)
            continue
        if lo <= ts <= hi:
            keep.append(_TS_RE.sub("", line, count=1))
            last_kept = True
        else:
            last_kept = False
    return "\n".join(keep)


def has_timestamps(text: str) -> bool:
    """判断文本中是否有任意行带行首 RFC3339 时间戳。

    用于检测旧 agent 未透传 timestamps 字段：旧 agent 忽略新字段，返回的日志没有时间戳前缀。
    """
    if not text:
        return False
    return any(_TS_RE.match(line) for line in text.splitlines())


def filter_logs_by_time_strict(text: str, start, end):
    """按 [start, end] 过滤带时间戳日志，返回 (text, error)。

    与 filter_logs_by_time 相同，但多一道校验：text 非空且无任何行带时间戳时返回 error
    （agent 版本过低，未透传 timestamps），调用方据此明确报错而不是静默返回空。
    """
    if not text:
        return "", ""
    if not has_timestamps(text):
        return "", "agent 版本过低，不支持按时间段下载，请升级 agent"
    return filter_logs_by_time(text, start, end), ""


async def is_agent_cluster(cluster_id: int) -> bool:
    """判断集群是否为 agent 接入（source=agent）。

    agent 接入集群平台侧无 kubeconfig → 日志流 9099 必 502，时间段下载/整份下载需改走 gRPC 隧道。
    平台直连（kubeconfig / in-cluster）返回 False。
    """
    from sqlalchemy import select

    from app.core.database import async_session
    from app.models import Cluster
    async with async_session() as session:
        r = await session.execute(select(Cluster.source).where(Cluster.id == int(cluster_id)))
        return (r.scalar_one_or_none() or "") == "agent"


async def fetch_logs(cluster_id: int, namespace: str, pod: str, container: str = "",
                     since_seconds: int = 0, all_lines: bool = False, previous: bool = False,
                     tail: int = 0, timeout: float = 30.0,
                     keep_timestamps: bool = False) -> tuple[str, str]:
    """取一次日志（follow=false）。返回 (text, error)。

    - since_seconds > 0：只取最近 N 秒（优先）
    - all_lines=True：不截断（整个日志文件）
    - 否则按 tail 行数
    - keep_timestamps=True：保留行首 RFC3339 时间戳（供按时间段过滤用），否则剥掉。
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
            return (resp.text if keep_timestamps else strip_timestamps(resp.text)), ""
    except Exception as e:  # noqa: BLE001
        logger.debug("日志流请求失败 cluster=%s %s/%s: %s", cluster_id, namespace, pod, e)
        return "", f"日志流不可用: {e}"
