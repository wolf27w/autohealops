# -*- coding: utf-8 -*-
"""用户管理 API"""
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.core.security import generate_api_key, hash_password
from app.middleware.permission import require_admin
from app.models import User

router = APIRouter(prefix="/api/v1/users")


@router.get("")
async def list_users(user=Depends(require_admin)):
    async with async_session() as session:
        result = await session.execute(select(User).order_by(User.id))
        users = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": u.id, "username": u.username, "email": u.email, "role": u.role,
         "is_active": u.is_active, "dingtalk_user_id": u.dingtalk_user_id, "wechat_userid": u.wechat_userid,
         "feishu_user_id": u.feishu_user_id or "", "feishu_open_id": u.feishu_open_id or "",
         "created_at": str(u.created_at)}
        for u in users
    ]}


VALID_ROLES = ("admin", "operator", "viewer", "developer")


@router.post("")
async def create_user(payload: dict, user=Depends(require_admin)):
    role = payload.get("role", "viewer")
    if role not in VALID_ROLES:
        return {"code": 400, "message": "非法角色", "data": None}
    async with async_session() as session:
        u = User(
            username=payload.get("username", ""),
            password_hash=hash_password(payload.get("password", "")),
            email=payload.get("email", ""),
            role=role,
            dingtalk_user_id=payload.get("dingtalk_user_id", ""),
            wechat_userid=payload.get("wechat_userid", ""),
            feishu_user_id=payload.get("feishu_user_id", ""),
            feishu_open_id=payload.get("feishu_open_id", ""),
        )
        session.add(u)
        await session.commit()
        await session.refresh(u)
        return {"code": 200, "message": "success", "data": {"id": u.id}}


@router.put("/{user_id}")
async def update_user(user_id: int, payload: dict, user=Depends(require_admin)):
    if "role" in payload and payload["role"] not in VALID_ROLES:
        return {"code": 400, "message": "非法角色", "data": None}
    async with async_session() as session:
        u = await session.get(User, user_id)
        if not u:
            return {"code": 404, "message": "用户不存在", "data": None}
        for k in ("email", "role", "is_active", "dingtalk_user_id", "wechat_userid",
                  "feishu_user_id", "feishu_open_id"):
            if k in payload:
                setattr(u, k, payload[k])
        if payload.get("password"):
            u.password_hash = hash_password(payload["password"])
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.delete("/{user_id}")
async def delete_user(user_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        u = await session.get(User, user_id)
        if u:
            await session.delete(u)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.post("/{user_id}/api-key")
async def regenerate_api_key(user_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        u = await session.get(User, user_id)
        if not u:
            return {"code": 404, "message": "用户不存在", "data": None}
        u.api_key = generate_api_key()
        await session.commit()
        return {"code": 200, "message": "success", "data": {"api_key": u.api_key}}
