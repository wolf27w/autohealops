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


async def _pump_logs(websocket: WebSocket, params: dict) -> None:
    base = getattr(settings, "EXECUTOR_LOGSTREAM_ADDR", "http://localhost:9099")
    try:
        async with httpx.AsyncClient(timeout=None, trust_env=False) as client:
            async with client.stream("GET", f"{base.rstrip('/')}/logs", params=params) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "ignore")
                    await websocket.send_json({"type": "error", "message": f"{resp.status_code} {body[:300]}"})
                    return
                async for chunk in resp.aiter_text():
                    if chunk:
                        await websocket.send_text(chunk)
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
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
