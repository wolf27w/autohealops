# -*- coding: utf-8 -*-
"""R20 僵尸服务检测 API（只读）"""
import json

from fastapi import APIRouter, Depends, HTTPException

from app.middleware.permission import require_admin, require_any_user
from app.models import User
from app.services import user_permission_service
from app.services import zombie_service
from app.services.system_setting_service import update_settings

router = APIRouter(prefix="/api/v1/zombie")


@router.get("/scan")
async def scan(cluster_id: int, namespace: str = "", days: int = 0, user: User = Depends(require_any_user)):
    """对某集群做一次僵尸服务检测（只读，不落库）"""
    if user.role != "admin":
        ok = await user_permission_service.check_cluster_permission(user, cluster_id, namespace or "", "view")
        if not ok:
            raise HTTPException(status_code=403, detail="无该集群查看权限")
    data = await zombie_service.scan(cluster_id, namespace=namespace, days=(days or None))
    return data


@router.get("/config")
async def get_config(user=Depends(require_admin)):
    cfg = await zombie_service.load_config()
    return {"code": 200, "message": "success", "data": {
        "config": cfg, "defaults": zombie_service.DEFAULT_CONFIG}}


@router.put("/config")
async def put_config(payload: dict, user=Depends(require_admin)):
    cfg = payload.get("config") if isinstance(payload.get("config"), dict) else payload
    merged = dict(zombie_service.DEFAULT_CONFIG)
    merged.update({k: v for k, v in (cfg or {}).items() if k in zombie_service.DEFAULT_CONFIG})
    await update_settings({zombie_service.CONFIG_KEY: json.dumps(merged, ensure_ascii=False)})
    return {"code": 200, "message": "success", "data": {"config": merged}}
