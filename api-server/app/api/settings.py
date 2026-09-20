# -*- coding: utf-8 -*-
"""系统设置 API：平台级配置（Prometheus/ES/LDAP）+ 密码修改"""
from fastapi import APIRouter, Depends

from app.core.security import hash_password, verify_password
from app.core.database import async_session
from app.middleware.auth import get_current_active_user
from app.middleware.permission import require_admin
from app.models import User
from app.services import system_setting_service

router = APIRouter(prefix="/api/v1")


@router.get("/settings")
async def get_settings(user=Depends(require_admin)):
    """读取全部系统设置（admin）"""
    settings = await system_setting_service.get_all_settings()
    return {"code": 200, "message": "success", "data": settings}


@router.put("/settings")
async def update_settings(payload: dict, user=Depends(require_admin)):
    """更新系统设置（admin）"""
    await system_setting_service.update_settings(payload)
    return {"code": 200, "message": "success", "data": None}


@router.post("/auth/change-password")
async def change_password(payload: dict, user=Depends(get_current_active_user)):
    """修改当前用户密码"""
    old_password = payload.get("old_password", "")
    new_password = payload.get("new_password", "")
    if not old_password or not new_password:
        return {"code": 400, "message": "旧密码和新密码不能为空", "data": None}
    if not verify_password(old_password, user.password_hash):
        return {"code": 400, "message": "旧密码错误", "data": None}
    async with async_session() as session:
        u = await session.get(User, user.id)
        u.password_hash = hash_password(new_password)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}
