# -*- coding: utf-8 -*-
"""日志查询 API（ES 数据源，支持按日志源查询；不传日志源走默认源）"""
import asyncio

import httpx
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.api.terminal import _resolve_user
from app.core.config import settings
from app.middleware.permission import require_any_user
from app.services.es_client import es_client
from app.services.user_permission_service import check_cluster_permission

router = APIRouter(prefix="/api/v1/logs")


@router.get("/search")
async def search_logs(cluster_name: str = "", namespace: str = "", pod: str = "", tail: int = 100,
                      cluster_id: int | None = None, log_source_id: int | None = None,
                      user=Depends(require_any_user)):
    """日志查询（前端日志查看入口，数据源为 Elasticsearch）。

    log_source_id 指定时用该日志源；不传走默认源（is_default 源，否则全局 ES 配置）。
    传 cluster_id 时做资源级鉴权。
    """
    if cluster_id and not await check_cluster_permission(user, cluster_id, namespace, "read"):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    try:
        res = await es_client.search_logs_result(namespace, pod, tail, log_source_id=log_source_id)
        logs, err = res.get("logs", ""), res.get("error", "")
        # 解析为行列表
        lines = logs.split("\n") if logs else []
        return {"code": 200, "message": "success", "data": {"logs": lines, "error": err, "source": res.get("source", "")}}
    except Exception as e:  # noqa: BLE001
        return {"code": 200, "message": "success", "data": {"logs": [], "error": f"日志查询异常：{e}"}}


# ============ 实时日志跟随（P1-R5）：WebSocket ============
# 浏览器 ↔ api-server(WS) ↔ executor 日志流 HTTP（apiserver follow）单向流式推送。


async def _pump_agent_logs(websocket: WebSocket, cluster_id: int, namespace: str, pod: str,
                          container: str, tail: int, since_seconds: int, previous: bool,
                          follow: bool) -> None:
    """agent 接入集群：平台侧无 kubeconfig，日志流 9099 必 502 → 改走 gRPC 隧道。

    非跟随：取一次（since_seconds 优先，否则 tail 行），空结果发 info 后关闭。
    跟随：每 2s 拉 tail 窗口，按行数增量推送（与聚合日志同一套增量口径）。
    """
    from app.api.workload_logs import incremental_new_lines
    from app.clients.executor_client import executor_client

    poll_tail = max(int(tail or 200), 300)

    async def _fetch(n: int) -> tuple[str, str]:
        """agent 集群直接走 gRPC（平台 9099 必 502）；返回 (text, error)。

        2026-11-23：与 kubeconfig 直连保持一致——直连的日志流会带 RFC3339 行首时间戳，
        agent 侧原来不带，导致两边显示不一样。这里同样请求 timestamps=True；
        老 agent 不支持时自动退回无时间戳（保证还能看）。
        """
        r = await executor_client.acall(
            executor_client.get_pod_logs, cluster_id, namespace, pod, container, n, previous, True,
        )
        if not (r or {}).get("success"):
            err = str((r or {}).get("error") or "")
            if "版本过低" in err or "not support" in err.lower() or "unsupported" in err.lower():
                r = await executor_client.acall(
                    executor_client.get_pod_logs, cluster_id, namespace, pod, container, n, previous, False,
                )
            if not (r or {}).get("success"):
                return "", str((r or {}).get("error") or "日志获取失败")
        return str((r or {}).get("logs") or ""), ""

    text, err = await _fetch(poll_tail)
    if err:
        await websocket.send_json({"type": "error", "message": f"日志获取失败：{err}"})
        return
    if text:
        await websocket.send_text(text)
    if not follow:
        if not text:
            await websocket.send_json({
                "type": "info",
                "message": "该时间范围内没有日志（点「实时日志」可持续跟随）",
            })
        try:
            await websocket.close(code=1000)
        except Exception:  # noqa: BLE001
            pass
        return

    pushed = len(text.split("\n")) if text else 0
    while True:
        await asyncio.sleep(2)
        t2, e2 = await _fetch(poll_tail)
        if e2:
            continue
        lines = t2.split("\n") if t2 else []
        new_lines, pushed = incremental_new_lines(lines, pushed)
        if new_lines:
            await websocket.send_text("\n".join(new_lines) + "\n")


async def _pump_logs(websocket: WebSocket, params: dict) -> None:
    base = getattr(settings, "EXECUTOR_LOGSTREAM_ADDR", "http://localhost:9099")
    sent_any = False
    follow = str(params.get("follow", "true")).lower() not in ("false", "0")
    try:
        from app.services.logstream_client import is_agent_cluster
        if await is_agent_cluster(int(params["cluster_id"])):
            await _pump_agent_logs(
                websocket, int(params["cluster_id"]), str(params["namespace"]), str(params["pod"]),
                str(params.get("container") or ""), int(params.get("tail") or 200),
                int(params.get("since_seconds") or 0),
                str(params.get("previous", "false")).lower() in ("true", "1"), follow,
            )
            return
        async with httpx.AsyncClient(timeout=None, trust_env=False) as client:
            async with client.stream("GET", f"{base.rstrip('/')}/logs", params=params) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "ignore")
                    await websocket.send_json({"type": "error", "message": f"{resp.status_code} {body[:300]}"})
                    return
                async for chunk in resp.aiter_text():
                    if chunk:
                        sent_any = True
                        await websocket.send_text(chunk)
        # 2026-11-23 修复：空结果必须明确告知，否则前端永远卡在「等待日志…」
        if not sent_any:
            await websocket.send_json({
                "type": "info",
                "message": "该时间范围内没有日志（点「实时日志」可持续跟随）",
            })
        # 非跟随模式流已结束 → 主动关闭，避免连接悬着
        if not follow:
            try:
                await websocket.close(code=1000)
            except Exception:  # noqa: BLE001
                pass
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        try:
            await websocket.send_json({"type": "error", "message": f"日志流异常：{e}"})
        except Exception:  # noqa: BLE001
            pass


@router.websocket("/ws/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/logs")
async def ws_pod_logs(websocket: WebSocket, cluster_id: int, namespace: str, pod_name: str):
    """实时日志跟随（WS）。query: token, container, tail, previous, follow"""
    token = websocket.query_params.get("token", "")
    user = await _resolve_user(token)
    if user is None:
        await websocket.close(code=4001, reason="未认证")
        return
    if not await check_cluster_permission(user, cluster_id, namespace, "read"):
        await websocket.close(code=4003, reason="无权限")
        return
    await websocket.accept()
    params = {
        "cluster_id": cluster_id, "namespace": namespace, "pod": pod_name,
        "container": websocket.query_params.get("container", ""),
        "tail": websocket.query_params.get("tail", "200"),
        "since_seconds": websocket.query_params.get("since_seconds", ""),
        "previous": websocket.query_params.get("previous", "false"),
        "follow": websocket.query_params.get("follow", "true"),
    }
    task = asyncio.create_task(_pump_logs(websocket, params))
    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        task.cancel()
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
