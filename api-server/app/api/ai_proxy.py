# -*- coding: utf-8 -*-
"""AI 服务代理路由：把 /api/v1/ai/* 转发到 ai-service（前端只连 api-server）"""
import httpx
from fastapi import APIRouter, Depends, Request

from app.core.config import settings
from app.core.http_client import get_http_client
from app.middleware.auth import get_current_active_user
from app.models import User

router = APIRouter(prefix="/api/v1/ai")

# 转发方法白名单
_ALLOWED_METHODS = {"GET", "POST", "PUT", "DELETE"}


def _internal_headers(headers: dict) -> dict:
    """构造转发请求头：剥离敏感头 + 附带内部服务 token（供 ai-service 校验）"""
    fwd = dict(headers)
    fwd.pop("host", None)
    fwd.pop("content-length", None)
    fwd.pop("authorization", None)  # 不外传用户 JWT 给 ai-service
    fwd["X-Internal-Token"] = settings.JWT_SECRET_KEY
    return fwd


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def ai_proxy(path: str, request: Request, user: User = Depends(get_current_active_user)):
    """通用代理：转发到 ai-service（已鉴权，仅登录用户可访问）"""
    if request.method not in _ALLOWED_METHODS:
        return {"code": 405, "message": "方法不允许", "data": None}
    url = f"{settings.AI_SERVICE_HTTP_ADDR}/api/v1/ai/{path}"
    if request.url.query:
        url += f"?{request.url.query}"

    headers = _internal_headers(request.headers)
    trace_id = getattr(request.state, "trace_id", "")
    if trace_id:
        headers["X-Trace-Id"] = trace_id

    body = await request.body()

    try:
        client = get_http_client(trust_env=False)
        resp = await client.request(
            request.method, url, headers=headers, content=body or None, timeout=120,
        )
        return resp.json()
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"AI 服务代理失败: {e}", "data": None}
