# -*- coding: utf-8 -*-
"""集群监控（Netdata）UI 反代：浏览器 → api-server → executor HTTP 反代 → 集群 netdata-parent:19999。

父服务仅 ClusterIP，不出网；鉴权统一在 api-server（复用集群读权限）。
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import settings
from app.core.database import async_session
from app.core.http_client import get_http_client
from app.middleware.permission import require_any_user
from app.models import Cluster
from app.services import user_permission_service

router = APIRouter(prefix="/api/v1/clusters")

# 逐跳头（反代时剥离，避免污染）
_HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "content-length", "host", "upgrade", "proxy-connection", "te"}

DEFAULT_NS = "apiops-monitoring"


async def _proxy(cluster_id: int, path: str, request: Request, user):
    if not await user_permission_service.check_cluster_permission(user, cluster_id):
        return JSONResponse({"code": 403, "message": "无权限访问该集群", "data": None}, status_code=403)
    async with async_session() as s:
        c = await s.get(Cluster, cluster_id)
    if not c:
        return JSONResponse({"code": 404, "message": "集群不存在", "data": None}, status_code=404)
    if not c.monitoring_enabled or c.monitoring_status != "running":
        return JSONResponse({"code": 400, "message": "该集群监控未启用或未就绪", "data": None}, status_code=400)
    ns = c.monitoring_ns or DEFAULT_NS

    upstream = f"{settings.EXECUTOR_MONITORING_PROXY_ADDR}/proxy/{cluster_id}/{ns}/{path}"
    if request.url.query:
        upstream += "?" + request.url.query

    in_headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_HEADERS}
    client = get_http_client()
    try:
        req = client.build_request(request.method, upstream, headers=in_headers, content=await request.body())
        resp = await client.send(req, stream=True)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"code": 502, "message": f"监控反代失败: {e}", "data": None}, status_code=502)

    out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _HOP_HEADERS}
    return StreamingResponse(resp.aiter_raw(), status_code=resp.status_code, headers=out_headers)


@router.get("/{cluster_id}/monitoring/ui")
async def monitoring_ui_root(cluster_id: int, request: Request, user=Depends(require_any_user)):
    return await _proxy(cluster_id, "", request, user)


@router.get("/{cluster_id}/monitoring/ui/{path:path}")
async def monitoring_ui_path(cluster_id: int, path: str, request: Request, user=Depends(require_any_user)):
    return await _proxy(cluster_id, path, request, user)
