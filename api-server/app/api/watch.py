# -*- coding: utf-8 -*-
"""实时状态推送 WebSocket（P1-R6）。

浏览器 ↔ api-server(WS) ↔ executor `/watch`（apiserver Pod/Deployment Watch，NDJSON 流）。
纯 HTTP 代理，零 proto 改动。
"""
import asyncio

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.terminal import _resolve_user
from app.core.config import settings
from app.services.user_permission_service import check_cluster_permission

router = APIRouter(prefix="/api/v1")


async def _pump(websocket: WebSocket, params: dict) -> None:
    base = getattr(settings, "EXECUTOR_LOGSTREAM_ADDR", "http://localhost:9099")
    try:
        async with httpx.AsyncClient(timeout=None, trust_env=False) as client:
            async with client.stream("GET", f"{base.rstrip('/')}/watch", params=params) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "ignore")
                    await websocket.send_json({"type": "error", "message": f"{resp.status_code} {body[:300]}"})
                    return
                async for line in resp.aiter_lines():
                    if line.strip():
                        await websocket.send_text(line)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        try:
            await websocket.send_json({"type": "error", "message": f"状态流异常：{e}"})
        except Exception:  # noqa: BLE001
            pass


@router.websocket("/ws/clusters/{cluster_id}/namespaces/{namespace}/pods/status")
async def ws_pod_status(websocket: WebSocket, cluster_id: int, namespace: str):
    """命名空间内 Pod（+可选 Deployment）实时状态流。query: token, name(可选 Deployment 名)"""
    token = websocket.query_params.get("token", "")
    user = await _resolve_user(token)
    if user is None:
        await websocket.close(code=4001, reason="未认证")
        return
    if not await check_cluster_permission(user, cluster_id, namespace, "read"):
        await websocket.close(code=4003, reason="无权限")
        return
    await websocket.accept()
    params = {"cluster_id": cluster_id, "namespace": namespace, "name": websocket.query_params.get("name", "")}
    task = asyncio.create_task(_pump(websocket, params))
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
