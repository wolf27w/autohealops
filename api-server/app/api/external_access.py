# -*- coding: utf-8 -*-
"""外部系统接入 API：token 鉴权，查询/创建工单（供外部监控/运维系统对接）

- token 存 system_settings.external_ticket_token（admin 可获取/重置）
- 外部调用带 header `X-External-Token: <token>`
- 建单 source=external，created_by=系统（空），全量可见
"""
import secrets
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Header, HTTPException

from app.middleware.permission import require_admin
from app.services import ticket_service
from app.services.system_setting_service import get_setting, update_settings

router = APIRouter(prefix="/api/v1/external")

EXTERNAL_TOKEN_KEY = "external_ticket_token"


def _gen_token() -> str:
    return secrets.token_hex(24)


async def _verify_token(x_external_token: str | None) -> bool:
    if not x_external_token:
        return False
    stored = await get_setting(EXTERNAL_TOKEN_KEY, "")
    return bool(stored) and secrets.compare_digest(stored, x_external_token.strip())


@router.get("/tickets/token")
async def get_external_token(user=Depends(require_admin)):
    """获取外部接入 token（admin；无则自动生成）"""
    token = await get_setting(EXTERNAL_TOKEN_KEY, "")
    if not token:
        token = _gen_token()
        await update_settings({EXTERNAL_TOKEN_KEY: token})
    return {"code": 200, "message": "success", "data": {"token": token}}


@router.post("/tickets/token/regenerate")
async def regenerate_external_token(user=Depends(require_admin)):
    """重置外部接入 token（admin）"""
    token = _gen_token()
    await update_settings({EXTERNAL_TOKEN_KEY: token})
    return {"code": 200, "message": "success", "data": {"token": token}}


@router.get("/tickets")
async def external_list_tickets(x_external_token: str | None = Header(None),
                                status: str | None = None, priority: str | None = None,
                                source: str | None = None, keyword: str | None = None,
                                page: int = 1, page_size: int = 20):
    """外部查询工单（token 鉴权，全量可见）"""
    if not await _verify_token(x_external_token):
        raise HTTPException(status_code=401, detail="invalid token")
    fake_admin = SimpleNamespace(id=None, role="admin", username="external")
    filters = {"status": status, "priority": priority, "source": source, "keyword": keyword}
    data = await ticket_service.list_tickets(filters, page, page_size, fake_admin)
    return {"code": 200, "message": "success", "data": data}


@router.post("/tickets")
async def external_create_ticket(payload: dict, x_external_token: str | None = Header(None)):
    """外部创建工单（token 鉴权，source=external）"""
    if not await _verify_token(x_external_token):
        raise HTTPException(status_code=401, detail="invalid token")
    r = await ticket_service._create_from_payload(payload, None, source="external",
                                                  alert_id=None, approval_id=None,
                                                  action_text="外部系统创建")
    if not r.get("ok"):
        return {"code": 400, "message": "建单失败", "data": None}
    t = r["ticket"]
    return {"code": 200, "message": "success", "data": {"id": t.id, "ticket_no": t.ticket_no, "status": t.status}}
