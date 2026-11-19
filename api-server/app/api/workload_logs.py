# -*- coding: utf-8 -*-
"""工作负载聚合日志（R5 剩余）：聚合日志 + SSE 跟随 + gzip 下载。

与 resources.py 里的单 Pod 日志（GetPodLogs）互补：
  2) GET .../workloads/{kind}/{name}/logs/stream   —— SSE，先推快照，再每 2s 轮询增量（跟随）
  3) GET .../pods/{pod}/logs/download              —— 单 Pod 日志附件下载（gzip 流式压缩）

设计口径：
  - 工作负载 → Pod 列表：deployment 走 executor ListDeploymentPods（精确），其余 kind
    走 ListPods 后按 `<name>-` 前缀匹配（与 resources.py 现有 `_workload_pod_names` 兜底一致）。
  - 排序：按 Pod created_at（起始时间）升序，Pod 内保持行序。
  - 返回为列式（columns=["pod","line"]），每行日志的「pod 前缀」由前端渲染（本模块提供
    prefix_line 纯函数等价实现），便于前端按 pod 折叠/过滤。
"""
import asyncio
import json
import logging
import time
import zlib
from datetime import datetime, timezone
from typing import AsyncIterator, Iterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select

from app.clients.executor_client import executor_client
from app.core.database import async_session
from app.core.security import decode_jwt
from app.middleware.permission import require_any_user
from app.models import User
from app.services.user_permission_service import check_cluster_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/resources")

_VALID_KINDS = {"deployment", "statefulset", "daemonset", "cronjob"}

# 聚合/跟随并发上限：get_pod_logs 是同步 gRPC，经 executor_client.acall 走线程池，
# 这里再限一层，避免一次性把线程池打满（与 resources.py 里信号量≤8 的口径一致）。
_AGG_SEMAPHORE = asyncio.Semaphore(6)

STREAM_TAIL = 500          # 跟随轮询每次拉的 tail 窗口（行）
STREAM_INTERVAL = 2.0      # 轮询间隔（秒）
STREAM_HEARTBEAT = 15.0    # 心跳注释帧间隔（秒）
MAX_AGG_TAIL = 5000        # 聚合 tail 上限
MAX_DOWNLOAD_TAIL = 5000   # 下载 tail 上限
_GZIP_CHUNK = 65536        # gzip 流式压缩块大小


# ==================== 纯函数（供单测） ====================

def prefix_line(pod: str, line: str) -> str:
    """给日志行加 pod 前缀（前端展示时等价实现）。"""
    return f"[{pod}] {line}"


def merge_log_lines(entries: list[tuple[str, list[str]]], prefix: bool = False) -> list[dict]:
    """把「已按 pod 起始时间排序」的多个 pod 日志行合并为列式行。

    entries: [(pod_name, [line, ...]), ...]，顺序即最终顺序（pod 起始时间 → 行序）。
    返回 [{pod, line}, ...]；prefix=True 时 line 带 `[pod] ` 前缀。
    """
    rows: list[dict] = []
    for pod, lines in entries:
        for line in lines:
            rows.append({"pod": pod, "line": prefix_line(pod, line) if prefix else line})
    return rows


def incremental_new_lines(lines: list[str], pushed: int) -> tuple[list[str], int]:
    """增量行数计算（跟随轮询）：返回 (新增行列表, 新已推送行数)。

    lines 是本次拉到的 tail 窗口，pushed 是上次已推送行数。
    窗口行数 >= pushed 时只推 lines[pushed:]；日志被截断/轮转（行数变小）时从头重推。
    """
    start = pushed if len(lines) >= pushed else 0
    return lines[start:], len(lines)


# ==================== 鉴权辅助 ====================

async def _deny_cluster(user, cluster_id: int, namespace: str = "", required: str = "read"):
    """与 resources.py 保持一致的集群资源级鉴权：无权限返回 403 dict，否则 None。"""
    if not await check_cluster_permission(user, cluster_id, namespace, required):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    return None


async def _resolve_user(token: str) -> User | None:
    """从 JWT token 解析用户（SSE/EventSource 无法带 Authorization 头，token 走 query param）。"""
    if not token:
        return None
    try:
        payload = decode_jwt(token)
    except Exception:  # noqa: BLE001
        return None
    try:
        user_id = int(payload.get("sub", 0) or 0)
    except (TypeError, ValueError):
        return None
    if not user_id:
        return None
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    return user if user and user.is_active else None


def _normalize_kind(kind: str) -> str:
    return (kind or "").strip().lower()


def _sort_pods(pods: list[dict]) -> list[dict]:
    """按 Pod 起始时间（created_at RFC3339）升序，兜底按名称。"""
    return sorted(pods, key=lambda p: (p.get("created_at") or "", p.get("name") or ""))


async def _list_workload_pods(cluster_id: int, namespace: str, kind: str, name: str) -> list[dict]:
    """列出工作负载下所有 Pod（含 created_at，用于排序）。

    deployment 走 ListDeploymentPods（精确，含新旧版本）；其余 kind 及 deployment 回退，
    用 ListPods + `<name>-` 前缀匹配（代码库既有兜底口径）。
    """
    all_r = await executor_client.acall(executor_client.list_pods, cluster_id, namespace)
    all_pods = all_r.get("pods") or [] if all_r.get("success") else []
    by_name = {p.get("name"): p for p in all_pods}

    if kind == "deployment" and name:
        dep_r = await executor_client.acall(executor_client.list_deployment_pods, cluster_id, namespace, name)
        if dep_r.get("success"):
            dep_pods: list[dict] = []
            for p in (dep_r.get("pods") or []):
                pn = p.get("name") or ""
                full = by_name.get(pn) or {}
                dep_pods.append({
                    "name": pn,
                    "created_at": full.get("created_at") or "",
                    "phase": full.get("phase") or p.get("phase") or "",
                })
            if dep_pods:
                return _sort_pods(dep_pods)

    matched = [p for p in all_pods if (p.get("name") or "").startswith(f"{name}-")]
    return _sort_pods(matched)


async def _fetch_pod_tail(cluster_id: int, namespace: str, pod: dict, container: str,
                          tail: int) -> tuple[dict, dict]:
    """拉单个 Pod 的 tail 日志（受 _AGG_SEMAPHORE 限流）。返回 (pod, result_dict)。"""
    async with _AGG_SEMAPHORE:
        r = await executor_client.acall(
            executor_client.get_pod_logs, cluster_id, namespace, pod.get("name"), container, tail, False)
    return pod, r


def _iter_gzip(text: str, chunk_size: int = _GZIP_CHUNK) -> Iterator[bytes]:
    """gzip 流式压缩：按块喂给 compressobj，避免一次性拼出完整压缩字节（源文本已在内存）。"""
    compressor = zlib.compressobj(6, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    data = text.encode("utf-8")
    for i in range(0, len(data), chunk_size):
        chunk = compressor.compress(data[i:i + chunk_size])
        if chunk:
            yield chunk
    tail = compressor.flush()
    if tail:
        yield tail


# ==================== 2) SSE 跟随 ====================

def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_events(request: Request, cluster_id: int, namespace: str, kind: str,
                         name: str, container: str) -> AsyncIterator[str]:
    """SSE 事件生成器：先推快照，再每 2s 轮询增量；15s 心跳；客户端断开即退出。"""
    pushed: dict[str, int] = {}   # pod → 已推送行数
    last_heartbeat = time.monotonic()
    first = True
    try:
        while True:
            if await request.is_disconnected():
                break
            pods = await _list_workload_pods(cluster_id, namespace, kind, name)
            results = await asyncio.gather(
                *[_fetch_pod_tail(cluster_id, namespace, p, container, STREAM_TAIL) for p in pods])
            current: set[str] = set()
            for pod, r in results:
                pname = pod.get("name") or ""
                current.add(pname)
                if not r.get("success"):
                    if first:
                        yield _sse_event("error", {"pod": pname, "error": r.get("error") or "日志获取失败"})
                    continue
                lines = (r.get("logs") or "").splitlines()
                new_lines, new_pushed = incremental_new_lines(lines, pushed.get(pname, 0))
                for line in new_lines:
                    yield _sse_event("log", {"pod": pname, "line": line})
                pushed[pname] = new_pushed
            # 清理已消失的 pod（避免内存里残留无意义条目）
            for pname in list(pushed.keys()):
                if pname not in current:
                    pushed.pop(pname, None)
            first = False
            if time.monotonic() - last_heartbeat >= STREAM_HEARTBEAT:
                yield ": keepalive\n\n"
                last_heartbeat = time.monotonic()
            await asyncio.sleep(STREAM_INTERVAL)
    except asyncio.CancelledError:
        raise
    except GeneratorExit:
        return
    except Exception as e:  # noqa: BLE001
        logger.warning("工作负载日志流异常 cluster=%s %s/%s/%s: %s", cluster_id, kind, namespace, name, e)
        try:
            yield _sse_event("error", {"pod": "", "error": str(e)})
        except Exception:  # noqa: BLE001
            pass


@router.get("/clusters/{cluster_id}/namespaces/{namespace}/workloads/{kind}/{name}/logs/stream")
async def workload_logs_stream(request: Request, cluster_id: int, namespace: str, kind: str, name: str,
                               container: str = "", token: str = ""):
    """聚合日志跟随（SSE）。EventSource 无法带 Authorization 头，token 走 query param
    （同时兼容 fetch + Authorization 头）。帧：event: log / event: error / 注释心跳帧。"""
    kind = _normalize_kind(kind)
    if kind not in _VALID_KINDS:
        raise HTTPException(status_code=400, detail=f"不支持的 kind：{kind or '(空)'}")

    user = await _resolve_user(token) if token else None
    if user is None:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            user = await _resolve_user(auth[7:].strip())
    if user is None:
        raise HTTPException(status_code=401, detail="未认证")
    if not await check_cluster_permission(user, cluster_id, namespace, "read"):
        raise HTTPException(status_code=403, detail="无权限访问该集群")

    return StreamingResponse(
        _stream_events(request, cluster_id, namespace, kind, name, container),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ==================== 3) gzip 下载 ====================

@router.get("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/logs/download")
async def pod_logs_download(cluster_id: int, namespace: str, pod_name: str, container: str = "",
                            tail: int = 5000, gzip: int = 1, start: str = "", end: str = "",
                            user=Depends(require_any_user)):
    """下载单 Pod 容器日志（附件）。gzip=1（默认）用 gzip 流式压缩，文件名带 pod 与时间戳。

    start/end（RFC3339 或 epoch 秒）同时给定时，按时间段过滤下载：走 executor 日志流 HTTP
    取整份带时间戳日志后在 api-server 侧按时间戳过滤（executor 只支持 since_seconds，不支持任意起止）。
    日志流不可用（agent 集群无平台 kubeconfig）时返回 502，由调用方决定是否降级为 tail 行数。
    """
    tail = max(1, min(int(tail or MAX_DOWNLOAD_TAIL), MAX_DOWNLOAD_TAIL))
    if denied := await _deny_cluster(user, cluster_id, namespace):
        return denied
    text = ""
    if start and end:
        from app.services.logstream_client import fetch_logs, filter_logs_by_time
        raw, err = await fetch_logs(cluster_id, namespace, pod_name, container,
                                    all_lines=True, keep_timestamps=True, timeout=120.0)
        if err:
            raise HTTPException(status_code=502, detail=f"日志流不可用，无法按时间段过滤：{err}")
        text = filter_logs_by_time(raw, start, end)
    else:
        r = await executor_client.acall(executor_client.get_pod_logs, cluster_id, namespace, pod_name,
                                        container, tail, False)
        text = r.get("logs", "") if r.get("success") else ""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    safe_ns = namespace or "default"
    safe_pod = pod_name or "pod"
    if int(gzip or 0):
        return StreamingResponse(
            _iter_gzip(text),
            media_type="application/gzip",
            headers={"Content-Disposition": f'attachment; filename="{safe_ns}_{safe_pod}_{ts}.log.gz"'},
        )
    return Response(
        content=text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_ns}_{safe_pod}_{ts}.log"'},
    )
