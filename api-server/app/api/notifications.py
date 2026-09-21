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
    is_admin = getattr(user, "role", "") == "admin"
    async with async_session() as session:
        result = await session.execute(select(NotificationConfig))
        configs = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": c.id, "name": c.name, "channel_type": c.channel_type,
         # admin 返回完整 URL：否则编辑时表单回填被截断的值，保存即写坏地址
         "webhook_url": c.webhook_url if is_admin else (c.webhook_url[:30] + "..."),
         "is_active": c.is_active,
         "scene": c.scene or "both", "namespaces": json.loads(c.namespaces or "[]"),
         "transport_mode": c.transport_mode, "feedback_mode": c.feedback_mode,
         "send_mode": c.send_mode or "webhook",
         "app_id": c.app_id or "", "receive_id": c.receive_id or "",
         "receive_id_type": c.receive_id_type or "chat_id",
         "stream_config": c.stream_config if is_admin else ("" if not c.stream_config else "***"),
         "im_template": c.im_template or "",
         "secret": (c.secret[:6] + "...") if c.secret else "",
         "app_secret": (c.app_secret[:6] + "...") if c.app_secret else ""}
        for c in configs
    ]}


def _validate_config(payload: dict, existing: NotificationConfig | None = None) -> str:
    """渠道配置校验（创建/更新共用），返回错误说明（""=通过）

    飞书支持两种发送方式：send_mode=app（应用机器人，需 App ID/Secret + 接收方）/ webhook（群机器人 URL）。
    """
    channel = payload.get("channel_type") or (existing.channel_type if existing else "")
    send_mode = payload.get("send_mode") or (existing.send_mode if existing else None) or "webhook"
    if send_mode == "app" and channel != "feishu":
        return "应用机器人模式仅支持飞书"
    if send_mode == "app":
        app_id = (payload.get("app_id") if "app_id" in payload else (existing.app_id if existing else "")) or ""
        app_secret = (payload.get("app_secret") if "app_secret" in payload else (existing.app_secret if existing else "")) or ""
        receive_id = (payload.get("receive_id") if "receive_id" in payload else (existing.receive_id if existing else "")) or ""
        if not app_id.strip() or not app_secret.strip():
            return "应用机器人模式需填 App ID / App Secret"
        if not receive_id.strip():
            return "应用机器人模式需填接收方（群 chat_id 或 用户 user_id）"
        return ""
    webhook_url = (payload.get("webhook_url") if "webhook_url" in payload else (existing.webhook_url if existing else "")) or ""
    if not webhook_url.strip():
        return "Webhook 模式需填群机器人 webhook 地址"
    return ""


@router.post("/configs")
async def create_config(payload: dict, user=Depends(require_admin)):
    err = _validate_config(payload)
    if err:
        return {"code": 400, "message": err, "data": None}
    async with async_session() as session:
        c = NotificationConfig(
            name=payload.get("name", ""),
            channel_type=payload.get("channel_type", ""),
            webhook_url=payload.get("webhook_url", ""),
            secret=payload.get("secret", ""),
            send_mode=payload.get("send_mode", "webhook"),
            app_id=payload.get("app_id", ""),
            app_secret=payload.get("app_secret", ""),
            receive_id=payload.get("receive_id", ""),
            receive_id_type=payload.get("receive_id_type", "chat_id"),
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
        err = _validate_config(payload, c)
        if err:
            return {"code": 400, "message": err, "data": None}
        for field in ("name", "channel_type", "webhook_url", "secret", "scene", "transport_mode", "feedback_mode", "stream_config",
                      "send_mode", "app_id", "app_secret", "receive_id", "receive_id_type"):
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
    if c.channel_type == "feishu" and (c.send_mode == "app" or (c.app_id or "").strip()):
        from app.services.feishu_service import send_feishu_message
        resp = await send_feishu_message(c.receive_id, c.receive_id_type or "chat_id",
                                         "【API-OPS】这是一条测试消息，渠道连通正常 ✅", c.app_id, c.app_secret)
        ok = resp.get("status") == "success"
        detail = resp
    else:
        resp = await messaging.send_text(c.channel_type, c.webhook_url, "【API-OPS】这是一条测试消息，渠道连通正常 ✅")
        ok = messaging.resp_ok(c.channel_type, resp, c.webhook_url)
        detail = resp
        if not ok and messaging.check_webhook_url(c.channel_type, c.webhook_url):
            detail = {**resp, "hint": messaging.check_webhook_url(c.channel_type, c.webhook_url)}
    return {"code": 200 if ok else 500, "message": "success" if ok else f"发送失败: {detail.get('hint') or detail.get('body', '')}",
            "data": {"ok": ok, "detail": detail}}


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
