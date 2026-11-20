# -*- coding: utf-8 -*-
"""异常 Pod 日志留存（R60 P2b）

背景：Pod 一被回收，日志就永久丢失，事后无法复盘崩溃原因（例如 qa-bgs/eft-gateway
失败发布连续 4 次起不来，Pod 回收后日志全没了）。

策略：
1. 扫描：遍历 clusters 表里 auto_heal_enabled=True 的集群，对每个集群调
   executor_client.list_pods(cluster_id, namespace="")（agent 集群自动走隧道）。
2. 筛选需要留存的 Pod：
   - 容器状态 waiting.reason ∈ {CrashLoopBackOff, ImagePullBackOff, ErrImagePull,
     CreateContainerConfigError, RunContainerError}
   - 或 restart_count 增长（进程内基线记录的上次 count 变大了）
   - 或 phase == Failed
3. 去重：同一 pod+container 在 30 分钟内只留存一次（进程内 dict）。
4. 抓日志：优先 get_pod_logs(previous=True)（上一次崩溃实例），失败再抓当前；
   单容器最多 5000 行，单文件上限 5MB。
5. 落盘：api-server/data/crash-logs/{cluster_id}/{namespace}/{pod}_{container}_{YYYYmmdd-HHMMSS}.log
   文件头一行元信息；路径存索引 index.json（控制在几千条以内，超了按时间淘汰）。
6. 清理：留存超过 CRASH_LOG_RETENTION_DAYS（默认 3）天，或总量超过 CRASH_LOG_MAX_MB（默认 200MB）的文件删除；gzip 落盘。

索引 index.json 结构（"entries" 数组，按 captured_at 倒序）：
{
  "entries": [
    {"pod": "...", "container": "...", "namespace": "...", "cluster_id": 1,
     "reason": "CrashLoopBackOff", "restart_count": 12,
     "captured_at": "2026-11-20T18:00:00", "path": "1/qa-bgs/pod_ctr_20261120-180000.log",
     "summary": "日志首行"}
  ]
}
"""
from __future__ import annotations

import asyncio
import json
import gzip
import os
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.clients.executor_client import executor_client
from app.core.database import async_session
from app.core.logging import get_logger
from app.models import Cluster

logger = get_logger("api-server.crash_log")

# 落盘根目录（api-server/data/crash-logs），可用环境变量 CRASH_LOG_DIR 覆盖
DEFAULT_CRASH_LOG_DIR = Path(__file__).resolve().parents[2] / "data" / "crash-logs"

# 需要留存的 waiting 原因集合（镜像拉取失败 / 配置错误 / 崩溃循环 / 运行错误）
CRASH_REASONS = frozenset({
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
    "CreateContainerConfigError",
    "RunContainerError",
})

# 去重窗口（秒）：同一 pod+container 30 分钟内只留存一次
DEDUP_WINDOW_SECONDS = 30 * 60  # 兜底去重窗口（正常按 restart_count 去重）

# 抓取上限
MAX_LOG_LINES = 3000       # 单容器最多取 3000 行（压缩后占用极小）
MAX_LOG_BYTES = 1 * 1024 * 1024   # 单文件上限 1MB（gzip 后通常几十 KB）
MAX_INDEX_ENTRIES = 1000   # 索引条数上限（超了按时间淘汰最旧的）
READ_TRUNCATE_BYTES = 200 * 1024  # read_crash_log 返回内容截断 200KB

# 后台扫描间隔（秒；main.py 用它注册后台任务）
SCAN_INTERVAL_SECONDS = 180


def _crash_log_dir() -> Path:
    """崩溃日志根目录：优先 CRASH_LOG_DIR，否则 data/crash-logs（解析为绝对路径）。"""
    raw = os.environ.get("CRASH_LOG_DIR", "").strip()
    p = Path(raw) if raw else DEFAULT_CRASH_LOG_DIR
    return p.resolve()


def _index_file() -> Path:
    return _crash_log_dir() / "index.json"


def _env_bool(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def crash_log_enabled() -> bool:
    """CRASH_LOG_ENABLED 开关（默认 true）。"""
    return _env_bool("CRASH_LOG_ENABLED", "true")


def retention_days() -> int:
    """CRASH_LOG_RETENTION_DAYS 保留天数（默认 7）。"""
    raw = os.environ.get("CRASH_LOG_RETENTION_DAYS", "3").strip()
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 7


# 进程内状态：去重时间戳 + 重启计数基线（不落库；进程重启后基线清零，首轮只记基线不误报）
_dedup: dict[str, float] = {}            # "cluster/ns/pod/container" -> 上次留存 epoch 秒
_restart_baseline: dict[str, int] = {}   # "cluster/ns/pod" -> pod 级重启计数
_container_baseline: dict[str, int] = {}  # "cluster/ns/pod/container" -> 容器级重启计数

# 索引读写锁（后台留存与读取接口并发时保护 index.json）
_index_lock = asyncio.Lock()


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _truncate_bytes(text: str, max_bytes: int) -> str:
    """按字节数安全截断（utf-8 边界安全）。"""
    b = text.encode("utf-8", "replace")
    if len(b) <= max_bytes:
        return text
    return b[:max_bytes].decode("utf-8", "ignore")


def _resolve_safe(rel_path: str) -> Path | None:
    """把索引里的相对路径解析成绝对路径；拒绝目录穿越/绝对路径/symlink 逃逸。返回 None=非法。"""
    if not rel_path or not isinstance(rel_path, str) or "\x00" in rel_path:
        return None
    try:
        p = Path(rel_path)
        if p.is_absolute():
            return None
        if ".." in p.parts:
            return None
        base = _crash_log_dir()
        resolved = (base / p).resolve()
        if resolved != base and not resolved.is_relative_to(base):
            return None
        return resolved
    except (OSError, ValueError):
        return None


async def _read_index() -> list[dict]:
    try:
        raw = await asyncio.to_thread(_index_file().read_text, "utf-8")
        obj = json.loads(raw)
        if isinstance(obj, dict) and isinstance(obj.get("entries"), list):
            return [e for e in obj["entries"] if isinstance(e, dict)]
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001
        logger.warning("读取崩溃日志索引失败: %s", e)
    return []


async def _write_index(entries: list[dict]) -> None:
    def _do() -> None:
        idx = _index_file()
        idx.parent.mkdir(parents=True, exist_ok=True)
        tmp = idx.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"entries": entries}, ensure_ascii=False), "utf-8")
        tmp.replace(idx)

    try:
        await asyncio.to_thread(_do)
    except Exception as e:  # noqa: BLE001
        logger.warning("写崩溃日志索引失败: %s", e)


async def _cleanup(retention: int) -> int:
    """删除超过保留期的文件 + 从索引剔除；返回剔除条数。"""
    entries = await _read_index()
    if not entries:
        return 0
    cutoff = datetime.now() - timedelta(days=retention)
    keep: list[dict] = []
    removed = 0
    for e in entries:
        ts = _parse_ts(e.get("captured_at"))
        if ts is not None and ts < cutoff:
            p = _resolve_safe(str(e.get("path") or ""))
            if p is not None:
                try:
                    p.unlink(missing_ok=True)
                except OSError:  # noqa: BLE001
                    pass
            removed += 1
        else:
            keep.append(e)
    if removed:
        await _write_index(keep)
    return removed


def crash_log_max_bytes() -> int:
    """崩溃日志总量上限（CRASH_LOG_MAX_MB，默认 200MB）。"""
    try:
        mb = int(os.environ.get("CRASH_LOG_MAX_MB", "200"))
    except ValueError:
        mb = 200
    return max(10, mb) * 1024 * 1024


def _dir_size() -> int:
    total = 0
    root = _crash_log_dir()
    if not root.exists():
        return 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:  # noqa: BLE001
            continue
    return total


async def _enforce_quota() -> int:
    """总量超限时按时间从最旧开始淘汰（连带删文件）。返回淘汰条数。"""
    limit = crash_log_max_bytes()
    if _dir_size() <= limit:
        return 0
    removed = 0
    async with _index_lock:
        entries = await _read_index()
        # 索引按插入顺序（最新在前）
        while entries and _dir_size() > limit:
            e = entries.pop()
            p = _resolve_safe(str(e.get("path") or ""))
            if p is not None:
                try:
                    p.unlink(missing_ok=True)
                except OSError:  # noqa: BLE001
                    pass
            removed += 1
        if removed:
            await _write_index(entries)
    if removed:
        logger.info("崩溃日志超出配额（%dMB），已淘汰 %d 份", limit // (1024 * 1024), removed)
    return removed


async def _capture_container(cluster_id: int, namespace: str, pod: str, container: str,
                             reason: str, restart: int, phase: str) -> int:
    """抓取并留存单个容器的崩溃日志；返回 1=已留存，0=去重跳过/无内容/失败。"""
    key = f"{cluster_id}/{namespace}/{pod}/{container}"
    now = datetime.now()
    # 按「崩溃实例」去重：restart_count 没变说明还是同一次崩溃，不必重复留存；
    # restart_count 变化 = 又崩了一次，才留新的一份。
    last = _dedup.get(key)
    if last is not None:
        last_restart, last_ts = last
        if int(restart or 0) <= int(last_restart or 0) and (now.timestamp() - last_ts) < DEDUP_WINDOW_SECONDS:
            return 0

    logs = ""
    source = ""
    # 优先上一次崩溃实例（previous=True），失败/为空再抓当前
    try:
        r = await executor_client.acall(
            executor_client.get_pod_logs, cluster_id, namespace, pod,
            container, MAX_LOG_LINES, True,
        )
        if r.get("success") and (r.get("logs") or "").strip():
            logs = r["logs"] or ""
            source = "previous"
    except Exception as e:  # noqa: BLE001
        logger.debug("抓取 %s/%s/%s previous 日志失败: %s", namespace, pod, container, e)
    if not logs:
        try:
            r2 = await executor_client.acall(
                executor_client.get_pod_logs, cluster_id, namespace, pod,
                container, MAX_LOG_LINES, False,
            )
            if r2.get("success"):
                logs = r2["logs"] or ""
                if logs.strip():
                    source = "current"
        except Exception as e:  # noqa: BLE001
            logger.debug("抓取 %s/%s/%s 当前日志失败: %s", namespace, pod, container, e)
    if not logs or not logs.strip():
        return 0

    logs = _truncate_bytes(logs, MAX_LOG_BYTES)
    summary = next((ln for ln in logs.splitlines() if ln.strip()), "").strip()[:200]

    ts = now.strftime("%Y%m%d-%H%M%S")
    safe_pod = str(pod).replace("/", "_")
    safe_container = str(container).replace("/", "_")
    rel_path = f"{cluster_id}/{namespace}/{safe_pod}_{safe_container}_{ts}.log.gz"
    abs_path = _crash_log_dir() / rel_path

    header = (
        f"captured_at={now.isoformat(timespec='seconds')} cluster_id={cluster_id} "
        f"namespace={namespace} pod={pod} container={container} "
        f"status={phase} reason={reason} restart_count={restart} source={source}\n"
    )
    try:
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        # gzip 落盘：日志压缩比通常 5~10 倍，显著降低磁盘占用
        with gzip.open(abs_path, "wt", encoding="utf-8") as f:
            f.write(header)
            f.write(logs)
    except Exception as e:  # noqa: BLE001
        logger.warning("崩溃日志落盘失败 %s: %s", rel_path, e)
        return 0

    entry = {
        "pod": pod,
        "container": container,
        "namespace": namespace,
        "cluster_id": int(cluster_id),
        "reason": reason,
        "restart_count": int(restart or 0),
        "captured_at": now.isoformat(timespec="seconds"),
        "path": rel_path,
        "summary": summary,
    }
    # 追加索引 + 超限按时间淘汰最旧（淘汰的连同文件一起删）
    async with _index_lock:
        entries = await _read_index()
        entries.insert(0, entry)
        overflow = entries[MAX_INDEX_ENTRIES:]
        del entries[MAX_INDEX_ENTRIES:]
        await _write_index(entries)
    for e in overflow:
        p = _resolve_safe(str(e.get("path") or ""))
        if p is not None:
            try:
                p.unlink(missing_ok=True)
            except OSError:  # noqa: BLE001
                pass

    await _enforce_quota()
    _dedup[key] = (int(restart or 0), now.timestamp())
    logger.info("已留存崩溃日志 %s/%s/%s（reason=%s restart=%d）→ %s",
                namespace, pod, container, reason, restart, rel_path)
    return 1


async def _scan_cluster(cluster_id: int, budget: int) -> int:
    """扫描单集群并留存崩溃日志；返回本轮该集群留存条数（不超过 budget）。"""
    if budget <= 0:
        return 0
    r = await executor_client.acall(executor_client.list_pods, cluster_id, "")
    if not r.get("success"):
        return 0
    pods = r.get("pods") or []
    captured = 0
    for pod in pods:
        if captured >= budget:
            break
        namespace = pod.get("namespace") or ""
        name = pod.get("name") or ""
        if not namespace or not name:
            continue
        phase = pod.get("phase") or ""
        status = pod.get("status") or ""
        restart = int(pod.get("restart_count") or 0)
        pod_key = f"{cluster_id}/{namespace}/{name}"
        prev_restart = _restart_baseline.get(pod_key)
        _restart_baseline[pod_key] = restart

        is_failed = phase == "Failed"
        restart_grew = prev_restart is not None and restart > prev_restart
        status_signal = any(x in status for x in CRASH_REASONS)
        if not (is_failed or restart_grew or status_signal):
            continue

        # 取容器级状态（waiting.reason / 容器级重启计数）
        try:
            detail = await executor_client.acall(executor_client.get_pod_status,
                                                 cluster_id, namespace, name)
        except Exception as e:  # noqa: BLE001
            logger.debug("get_pod_status %s/%s 失败: %s", namespace, name, e)
            continue
        if not detail.get("success"):
            continue

        for c in detail.get("containers") or []:
            if captured >= budget:
                break
            cname = c.get("name") or ""
            cstate = c.get("state") or ""
            creason = c.get("reason") or ""
            crestarts = int(c.get("restart_count") or 0)
            if not cname:
                continue
            ckey = f"{pod_key}/{cname}"
            cprev = _container_baseline.get(ckey)
            _container_baseline[ckey] = crestarts

            waiting_crash = cstate == "waiting" and creason in CRASH_REASONS
            c_restart_grew = cprev is not None and crestarts > cprev
            if not (is_failed or waiting_crash or c_restart_grew):
                continue

            reason = creason or (status if is_failed else "") or "unknown"
            captured += await _capture_container(cluster_id, namespace, name, cname,
                                                 reason, crestarts, phase)
    return captured


async def collect_crash_logs(limit: int = 50) -> int:
    """后台留存主入口：扫描全部开启集群，留存异常 Pod 崩溃日志；返回本轮留存条数。

    limit=单轮最多留存条数（防失控）；任何单 Pod/单集群失败不影响整体。
    CRASH_LOG_ENABLED=false 时直接返回 0。
    """
    if not crash_log_enabled():
        return 0
    if limit <= 0:
        return 0

    captured = 0
    # 先清理过期文件 + 索引（超限按时间淘汰在 _capture_container 里做）
    try:
        await _cleanup(retention_days())
    except Exception as e:  # noqa: BLE001
        logger.warning("崩溃日志清理异常: %s", e)

    try:
        async with async_session() as session:
            clusters = list((await session.execute(
                select(Cluster).where(Cluster.auto_heal_enabled.is_(True))
            )).scalars())
    except Exception as e:  # noqa: BLE001
        logger.warning("崩溃日志扫描获取集群列表失败: %s", e)
        return captured

    for c in clusters:
        if captured >= limit:
            break
        try:
            captured += await _scan_cluster(c.id, limit - captured)
        except Exception as e:  # noqa: BLE001
            logger.warning("集群 %s 崩溃日志扫描异常: %s", c.id, e)
    return captured


async def list_crash_logs(cluster_id: int | None, namespace: str,
                          workload: str = "", hours: int = 24) -> list[dict]:
    """按 cluster/namespace/workload（pod 名前缀）/hours 过滤索引，按时间倒序返回。

    返回元素：{pod, container, namespace, cluster_id, reason, restart_count,
               captured_at, path, summary}。
    """
    entries = await _read_index()
    cutoff = datetime.now() - timedelta(hours=max(0, int(hours or 24)))
    result: list[dict] = []
    for e in entries:
        if cluster_id is not None and int(e.get("cluster_id", -1)) != int(cluster_id):
            continue
        if namespace and e.get("namespace") != namespace:
            continue
        if workload and not (e.get("pod") or "").startswith(workload):
            continue
        ts = _parse_ts(e.get("captured_at"))
        if ts is not None and ts < cutoff:
            continue
        result.append({
            "pod": e.get("pod"),
            "container": e.get("container"),
            "namespace": e.get("namespace"),
            "cluster_id": e.get("cluster_id"),
            "reason": e.get("reason"),
            "restart_count": e.get("restart_count"),
            "captured_at": e.get("captured_at"),
            "path": e.get("path"),
            "summary": e.get("summary"),
        })
    result.sort(key=lambda x: str(x.get("captured_at") or ""), reverse=True)
    return result


async def read_crash_log(rel_path: str) -> str:
    """安全读取崩溃日志内容（截断 200KB）。

    - 仅允许 data/crash-logs 下的相对路径；`..` / 绝对路径 / symlink 逃逸 → 抛 ValueError。
    - 文件不存在或为空 → 返回空串。
    """
    p = _resolve_safe(rel_path)
    if p is None:
        raise ValueError("非法路径：仅允许访问 crash-logs 目录内的相对路径")

    def _read() -> str:
        try:
            opener = gzip.open if p.suffix == ".gz" else open
            with opener(p, "rt", encoding="utf-8", errors="replace") as f:
                return f.read(READ_TRUNCATE_BYTES)
        except FileNotFoundError:
            return ""
        except OSError:
            return ""

    return await asyncio.to_thread(_read)
