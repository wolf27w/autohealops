# -*- coding: utf-8 -*-
"""工作负载聚合日志（R5 剩余）：聚合日志 + SSE 跟随。

与 resources.py 里的单 Pod 日志（GetPodLogs）互补：
  GET .../workloads/{kind}/{name}/logs/stream   —— SSE，先推快照，再每 2s 轮询增量（跟随）

单 Pod 日志下载端点统一在 resources.py（GET .../pods/{pod}/logs/download），勿在此重复定义。

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
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.clients.executor_client import executor_client
from app.core.database import async_session
from app.core.security import decode_jwt
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


def incremental_new_lines(lines: list[str], pushed: int, last_line: str = "") -> tuple[list[str], int]:
    """增量行计算（跟随轮询）：返回 (新增行列表, 新已推送行数)。

    ⚠️ 2026-11-23 修 bug：原来只比对行数。tail 窗口固定 500 行时，
    窗口一旦填满（len==pushed==500）就永远算不出新增 → 实时日志涨到窗口上限后不再更新
    （实测 400 多行就停住）。现在优先用「上次推过的最后一行」定位，窗口滑动也能正确增量；
    找不到该行（日志轮转/重启）时回退行数比较。
    """
    if not lines:
        return [], pushed
    if last_line:
        for i in range(len(lines) - 1, -1, -1):
            if lines[i] == last_line:
                return lines[i + 1:], len(lines)
    start = pushed if len(lines) >= pushed else 0
    return lines[start:], len(lines)


# ==================== 鉴权辅助 ====================

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


# 2026-11-23 老板口径：日志采集类 sidecar 不属于业务日志，默认不聚合
# （需要在日志里排查采集器本身时，可在容器下拉里显式选择）。
LOG_COLLECTOR_CONTAINERS = {
    "filebeat", "filebeat-sidecar", "fluent-bit", "fluentd", "logstash",
    "vector", "logtail", "log-pilot", "promtail",
}


def _is_log_collector(name: str) -> bool:
    return (name or "").strip().lower() in LOG_COLLECTOR_CONTAINERS


async def _workload_containers(cluster_id: int, kind: str, namespace: str, name: str) -> list[str]:
    """取工作负载模板里的业务容器名（排除日志采集 sidecar）。

    用模板而不是逐 Pod 查详情：模板已是业务容器（filebeat 这类注入容器不在里面），
    且一次调用搞定，避免 N 个 Pod × get_pod_detail 把聚合拖到超时
    （2026-11-23 实测：逐 Pod 详情会让聚合接口 40s 不返回、前端只能显示一行）。
    """
    if not kind or not name:
        return []
    try:
        r = await executor_client.acall(
            executor_client.get_workload_yaml, cluster_id, kind, namespace, name)
        if not (r or {}).get("success"):
            return []
        import yaml as _yaml
        doc = _yaml.safe_load((r or {}).get("yaml") or "") or {}
        tpl = (((doc.get("spec") or {}).get("template") or {}).get("spec") or {})
        names = [str(c.get("name")) for c in (tpl.get("containers") or []) if c.get("name")]
        business = [n for n in names if not _is_log_collector(n)]
        return business or names
    except Exception:  # noqa: BLE001
        return []


async def _fetch_pod_tail(cluster_id: int, namespace: str, pod: dict, container: str,
                          tail: int, kind: str = "", name: str = "") -> tuple[dict, dict]:
    """拉单个 Pod 的 tail 日志（受 _AGG_SEMAPHORE 限流）。返回 (pod, result_dict)。

    2026-11-23：`container` 为空 = **聚合该 Pod 所有容器**（含 sidecar），
    每行加 `[容器名] ` 前缀；原来容器为空时多容器 Pod 会直接报
    "a container name must be specified"，导致“聚合日志”实际只跟一个容器。
    """
    pname = pod.get("name")
    async with _AGG_SEMAPHORE:
        if container:
            r = await executor_client.acall(
                executor_client.get_pod_logs, cluster_id, namespace, pname, container, tail, False)
            return pod, r
        names = await _workload_containers(cluster_id, kind, namespace, name)
        if not names:
            r = await executor_client.acall(
                executor_client.get_pod_logs, cluster_id, namespace, pname, "", tail, False)
            return pod, r
        parts: list[str] = []
        errors: list[str] = []
        multi = len(names) > 1
        for cname in names:
            r = await executor_client.acall(
                executor_client.get_pod_logs, cluster_id, namespace, pname, cname, tail, False)
            if (r or {}).get("success"):
                text = (r or {}).get("logs") or ""
                if multi:
                    text = "\n".join(f"[{cname}] {ln}" for ln in text.split("\n") if ln)
                if text:
                    parts.append(text)
            else:
                errors.append(f"{cname}: {str((r or {}).get('error'))[:80]}")
        return pod, {"success": bool(parts) or not errors, "logs": "\n".join(parts),
                     "error": "；".join(errors)}


# ==================== 2) SSE 跟随 ====================

def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_events(request: Request, cluster_id: int, namespace: str, kind: str,
                         name: str, container: str) -> AsyncIterator[str]:
    """SSE 事件生成器：先推快照，再每 2s 轮询增量；15s 心跳；客户端断开即退出。"""
    pushed: dict[str, int] = {}   # pod → 已推送行数
    last_pushed: dict[str, str] = {}   # pod → 上次推送的最后一行（窗口滑动的增量锚点）
    last_heartbeat = time.monotonic()
    first = True
    try:
        while True:
            if await request.is_disconnected():
                break
            pods = await _list_workload_pods(cluster_id, namespace, kind, name)
            results = await asyncio.gather(
                *[_fetch_pod_tail(cluster_id, namespace, p, container, STREAM_TAIL, kind, name) for p in pods])
            current: set[str] = set()
            for pod, r in results:
                pname = pod.get("name") or ""
                current.add(pname)
                if not r.get("success"):
                    if first:
                        yield _sse_event("error", {"pod": pname, "error": r.get("error") or "日志获取失败"})
                    continue
                lines = (r.get("logs") or "").splitlines()
                new_lines, new_pushed = incremental_new_lines(
                    lines, pushed.get(pname, 0), last_pushed.get(pname, ""))
                for line in new_lines:
                    yield _sse_event("log", {"pod": pname, "line": line})
                pushed[pname] = new_pushed
                if new_lines:
                    last_pushed[pname] = new_lines[-1]
            # 清理已消失的 pod（避免内存里残留无意义条目）
            for pname in list(pushed.keys()):
                if pname not in current:
                    pushed.pop(pname, None)
                    last_pushed.pop(pname, None)
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
# 单 Pod 日志下载端点统一在 resources.py（GET .../pods/{pod}/logs/download，
# 支持 previous/all/agent 集群）。勿在此重复定义同一路由。
