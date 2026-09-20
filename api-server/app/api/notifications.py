# -*- coding: utf-8 -*-
"""通知配置 API"""
import json

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_admin, require_any_user
from app.models import NotificationConfig, NotificationLog
from app.services import notification_service

router = APIRouter(prefix="/api/v1/notifications")


@router.get("/policy")
async def get_notify_policy(user=Depends(require_any_user)):
    """读取全平台统一告警通知策略（分级渠道 + 静默窗口，适用范围=全部告警）"""
    policy = await notification_service.get_alert_notify_policy()
    return {"code": 200, "message": "success", "data": policy}


@router.put("/policy")
async def update_notify_policy(payload: dict, user=Depends(require_admin)):
    """更新全平台统一告警通知策略（admin）"""
    await notification_service.update_alert_notify_policy(payload)
    return {"code": 200, "message": "success", "data": None}


@router.get("/configs")
async def list_configs(user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(select(NotificationConfig))
        configs = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": c.id, "name": c.name, "channel_type": c.channel_type,
         "webhook_url": c.webhook_url[:30] + "...", "is_active": c.is_active,
         "scene": c.scene or "both", "namespaces": json.loads(c.namespaces or "[]"),
         "transport_mode": c.transport_mode, "feedback_mode": c.feedback_mode,
         "im_template": c.im_template or "",
         "secret": (c.secret[:6] + "...") if c.secret else ""}
        for c in configs
    ]}


@router.post("/configs")
async def create_config(payload: dict, user=Depends(require_admin)):
    async with async_session() as session:
        c = NotificationConfig(
            name=payload.get("name", ""),
            channel_type=payload.get("channel_type", ""),
            webhook_url=payload.get("webhook_url", ""),
            secret=payload.get("secret", ""),
            scene=payload.get("scene", "both"),
            namespaces=json.dumps(payload.get("namespaces", []), ensure_ascii=False),
            transport_mode=payload.get("transport_mode", "webhook"),
            feedback_mode=payload.get("feedback_mode", "text"),
            stream_config=payload.get("stream_config", ""),
            im_template=payload.get("im_template", ""),
        )
        session.add(c)
        await session.commit()
        await session.refresh(c)
        return {"code": 200, "message": "success", "data": {"id": c.id}}


@router.put("/configs/{config_id}")
async def update_config(config_id: int, payload: dict, user=Depends(require_admin)):
    async with async_session() as session:
        c = await session.get(NotificationConfig, config_id)
        if not c:
            return {"code": 404, "message": "配置不存在", "data": None}
        for field in ("name", "channel_type", "webhook_url", "secret", "scene", "transport_mode", "feedback_mode", "stream_config"):
            if field in payload:
                setattr(c, field, payload[field])
        if "im_template" in payload:
            c.im_template = payload.get("im_template", "")
        if "namespaces" in payload:
            c.namespaces = json.dumps(payload["namespaces"], ensure_ascii=False)
        if "is_active" in payload:
            c.is_active = payload["is_active"]
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.post("/configs/{config_id}/test")
async def test_config(config_id: int, user=Depends(require_admin)):
    """发送测试消息验证渠道连通"""
    from app.services import messaging
    async with async_session() as session:
        c = await session.get(NotificationConfig, config_id)
        if not c:
            return {"code": 404, "message": "配置不存在", "data": None}
    resp = await messaging.send_text(c.channel_type, c.webhook_url, "【API-OPS】这是一条测试消息，渠道连通正常 ✅")
    ok = resp["status"] in (200, "success")
    return {"code": 200 if ok else 500, "message": "success" if ok else f"发送失败: {resp.get('body', '')}",
            "data": {"ok": ok, "detail": resp}}


@router.delete("/configs/{config_id}")
async def delete_config(config_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        c = await session.get(NotificationConfig, config_id)
        if c:
            await session.delete(c)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.get("/logs")
async def list_logs(user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(select(NotificationLog).order_by(NotificationLog.id.desc()).limit(50))
        logs = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": l.id, "config_id": l.config_id, "alert_id": l.alert_id, "status": l.status,
         "sent_at": str(l.sent_at)}
        for l in logs
    ]}
