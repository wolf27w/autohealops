# -*- coding: utf-8 -*-
"""告警 AI 处置台 API（2026-11-26）：候选动作 + 授权判定 + 一键触发。"""
from fastapi import APIRouter, Depends

from app.core.database import async_session
from app.middleware.permission import require_operator
from app.models import Alert
from app.services import alert_action_service

router = APIRouter(prefix="/api/v1/alerts", tags=["alert-actions"])


@router.get("/{alert_id}/candidate-actions")
async def get_candidate_actions(alert_id: int, user=Depends(require_operator)):
    """候选处置动作 + 当前策略判定（只读）"""
    async with async_session() as session:
        alert = await session.get(Alert, alert_id)
    if alert is None:
        return {"code": 404, "message": "告警不存在", "data": None}
    data = await alert_action_service.candidate_actions(alert)
    return {"code": 200, "message": "success", "data": data}


@router.post("/{alert_id}/dispatch")
async def dispatch_action(alert_id: int, payload: dict, user=Depends(require_operator)):
    """触发处置：{"action": "ai" | "manual"}"""
    action = str((payload or {}).get("action") or "").strip().lower()
    operator = getattr(user, "username", "") or ""
    r = await alert_action_service.dispatch(alert_id, action, operator)
    if not r.get("ok"):
        return {"code": 400, "message": r.get("error") or "处置失败", "data": None}
    return {"code": 200, "message": "success", "data": r}
