# -*- coding: utf-8 -*-
"""消息群 API：群 CRUD（审批应用群 chat_id / 告警 webhook 群）"""
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_admin, require_any_user
from app.models import IMGroup

router = APIRouter(prefix="/api/v1/im-groups")


def _group_dict(g: IMGroup) -> dict:
    return {
        "id": g.id, "name": g.name, "channel_type": g.channel_type,
        "chat_id": g.chat_id, "webhook_url": g.webhook_url,
        "is_active": g.is_active, "created_at": str(g.created_at),
    }


@router.get("")
async def list_groups(user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(select(IMGroup).order_by(IMGroup.id.desc()))
        groups = list(result.scalars())
    return {"code": 200, "message": "success", "data": [_group_dict(g) for g in groups]}


@router.post("")
async def create_group(payload: dict, user=Depends(require_admin)):
    name = payload.get("name", "").strip()
    channel_type = payload.get("channel_type", "")
    if not name or channel_type not in ("feishu", "dingtalk", "wechat"):
        return {"code": 400, "message": "名称和渠道必填（feishu/dingtalk/wechat）", "data": None}
    # 应用群需要 chat_id，webhook 群需要 webhook_url，至少一个
    chat_id = payload.get("chat_id", "").strip()
    webhook_url = payload.get("webhook_url", "").strip()
    if not chat_id and not webhook_url:
        return {"code": 400, "message": "chat_id（应用群）或 webhook_url（告警群）至少填一个", "data": None}
    async with async_session() as session:
        g = IMGroup(
            name=name, channel_type=channel_type,
            chat_id=chat_id, webhook_url=webhook_url,
            is_active=payload.get("is_active", True),
        )
        session.add(g)
        await session.commit()
        await session.refresh(g)
        return {"code": 200, "message": "success", "data": _group_dict(g)}


@router.put("/{group_id}")
async def update_group(group_id: int, payload: dict, user=Depends(require_admin)):
    async with async_session() as session:
        g = await session.get(IMGroup, group_id)
        if not g:
            return {"code": 404, "message": "群不存在", "data": None}
        if payload.get("name", "").strip():
            g.name = payload["name"].strip()
        if "chat_id" in payload:
            g.chat_id = payload["chat_id"].strip()
        if "webhook_url" in payload:
            g.webhook_url = payload["webhook_url"].strip()
        if "is_active" in payload:
            g.is_active = payload["is_active"]
        await session.commit()
        await session.refresh(g)
    return {"code": 200, "message": "success", "data": _group_dict(g)}


@router.delete("/{group_id}")
async def delete_group(group_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        g = await session.get(IMGroup, group_id)
        if not g:
            return {"code": 404, "message": "群不存在", "data": None}
        await session.delete(g)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}
