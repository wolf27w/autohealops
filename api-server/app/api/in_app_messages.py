# -*- coding: utf-8 -*-
"""站内消息 API：右上角铃铛消息中心（未读计数 + 列表 + 已读）"""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.core.database import async_session
from app.middleware.permission import require_any_user
from app.models import InAppMessage

router = APIRouter(prefix="/api/v1/in-app-messages")


@router.get("/unread-count")
async def unread_count(user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(
            select(func.count()).select_from(InAppMessage).where(
                InAppMessage.user_id == user.id, InAppMessage.is_read.is_(False)
            )
        )
        count = result.scalar() or 0
    return {"code": 200, "message": "success", "data": {"count": count}}


@router.get("")
async def list_messages(unread_only: bool = False, page: int = 1, page_size: int = 50,
                        user=Depends(require_any_user)):
    async with async_session() as session:
        query = select(InAppMessage).where(InAppMessage.user_id == user.id)
        if unread_only:
            query = query.where(InAppMessage.is_read.is_(False))
        result = await session.execute(
            query.order_by(InAppMessage.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
        messages = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": m.id, "title": m.title, "content": m.content, "kind": m.kind,
         "link": m.link, "is_read": bool(m.is_read), "created_at": str(m.created_at)}
        for m in messages
    ]}


@router.post("/{message_id}/read")
async def mark_read(message_id: int, user=Depends(require_any_user)):
    async with async_session() as session:
        m = await session.get(InAppMessage, message_id)
        if not m or m.user_id != user.id:
            return {"code": 404, "message": "消息不存在", "data": None}
        m.is_read = True
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.post("/read-all")
async def mark_all_read(user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(
            select(InAppMessage).where(InAppMessage.user_id == user.id, InAppMessage.is_read.is_(False))
        )
        for m in result.scalars():
            m.is_read = True
        await session.commit()
    return {"code": 200, "message": "success", "data": None}
