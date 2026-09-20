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
    # 涉及 Embedding 配置时，通知 ai-service 清缓存（改完立即生效，不用等 60s）
    if any(k.startswith("embedding_") for k in payload.keys()):
        try:
            from app.clients.ai_client import ai_client
            await ai_client.reset_embed_cache()
        except Exception:  # noqa: BLE001
            pass
    return {"code": 200, "message": "success", "data": None}


@router.post("/settings/embedding/test")
async def test_embedding(payload: dict | None = None, user=Depends(require_admin)):
    """Embedding 连通性测试（admin）

    payload 可传 {base_url, model, api_key, provider} 做「先测再存」；不传则测当前已保存配置。
    返回 {ok, dimension, model, base_url, error}，前端可直接展示结果。
    """
    from app.clients.ai_client import ai_client
    payload = payload or {}
    cfg = {k: payload.get(k) for k in ("provider", "base_url", "model", "api_key") if payload.get(k) is not None}
    if not cfg:
        cfg = None
    try:
        # ai_client._post 已解包 data（code!=200 时抛异常）
        data = await ai_client.embed_test(cfg)
    except Exception as e:  # noqa: BLE001
        data = {"ok": False, "error": str(e)}
    ok = bool((data or {}).get("ok"))
    return {"code": 200 if ok else 400,
            "message": "连通正常" if ok else ((data or {}).get("error") or "测试失败"),
            "data": data or {}}


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
