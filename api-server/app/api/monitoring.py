# -*- coding: utf-8 -*-
"""集群监控（Netdata）UI 反代：浏览器 → api-server → executor HTTP 反代 → 集群 netdata-parent:19999。

父服务仅 ClusterIP，不出网；鉴权统一在 api-server（复用集群读权限）。
iframe 场景无 Authorization 头，token 走 query param（同 terminal WebSocket 口径）。
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from jose import JWTError
from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session
from app.core.http_client import get_http_client
from app.core.security import decode_jwt
from app.models import Cluster, User
from app.services import user_permission_service

router = APIRouter(prefix="/api/v1/clusters")

# 逐跳头（反代时剥离，避免污染）
_HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "content-length", "host", "upgrade", "proxy-connection", "te"}

DEFAULT_NS = "apiops-monitoring"


async def _resolve_user(token: str):
    try:
        payload = decode_jwt(token)
    except JWTError:
        return None
    user_id = int(payload.get("sub", 0))
    async with async_session() as s:
        result = await s.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    return user if user and user.is_active else None


def _extract_token(request: Request) -> str:
    h = request.headers.get("Authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return request.query_params.get("token", "") or request.cookies.get("apiops_token", "")


async def _proxy(cluster_id: int, path: str, request: Request):
    user = await _resolve_user(_extract_token(request))
    if not user:
        return JSONResponse({"code": 401, "message": "未认证", "data": None}, status_code=401)
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
async def monitoring_ui_root(cluster_id: int, request: Request):
    return await _proxy(cluster_id, "", request)


@router.get("/{cluster_id}/monitoring/ui/{path:path}")
async def monitoring_ui_path(cluster_id: int, path: str, request: Request):
    return await _proxy(cluster_id, path, request)
