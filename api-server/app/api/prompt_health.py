# -*- coding: utf-8 -*-
"""R13 提示词模板健康度：采纳率 / 熔断状态 / 版本回滚 / 手动恢复"""
from fastapi import APIRouter, Depends

from app.middleware.permission import require_admin, require_any_user
from app.services import prompt_health_service as phs

router = APIRouter(prefix="/api/v1/prompt-health", tags=["提示词健康度"])


@router.get("")
async def health_list(user=Depends(require_any_user)):
    """模板健康度列表（采纳率/无效次数/状态/阈值）"""
    return {"code": 200, "message": "success", "data": await phs.health_list()}


@router.post("/sweep")
async def sweep(user=Depends(require_admin)):
    """立即体检所有 auto 模板（等价于每小时定时任务）"""
    return {"code": 200, "message": "success", "data": await phs.sweep()}


@router.get("/{template_id}/versions")
async def versions(template_id: int, user=Depends(require_any_user)):
    """历史版本列表（回滚用）"""
    return {"code": 200, "message": "success", "data": await phs.list_versions(template_id)}


@router.post("/{template_id}/rollback")
async def rollback(template_id: int, payload: dict | None = None, user=Depends(require_admin)):
    """回滚到上一版本（可传 version 指定），回滚后自动恢复上线"""
    version = (payload or {}).get("version")
    r = await phs.rollback(template_id, int(version) if version else None)
    if not r.get("ok"):
        return {"code": 400, "message": r.get("message", "回滚失败"), "data": None}
    return {"code": 200, "message": "success", "data": r}


@router.post("/{template_id}/reactivate")
async def reactivate(template_id: int, user=Depends(require_admin)):
    """手动恢复（熔断后人工确认可用）"""
    r = await phs.reactivate(template_id)
    if not r.get("ok"):
        return {"code": 400, "message": r.get("message", "恢复失败"), "data": None}
    return {"code": 200, "message": "success", "data": r}


@router.post("/{template_id}/check")
async def check(template_id: int, user=Depends(require_admin)):
    """单模板体检（按采纳率判定是否熔断）"""
    return {"code": 200, "message": "success", "data": await phs.check_template(template_id)}
