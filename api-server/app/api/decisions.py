# -*- coding: utf-8 -*-
"""决策策略 API"""
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_admin, require_any_user, require_operator
from app.models import DecisionPolicy
from app.services.decision_engine import evaluate_plan

router = APIRouter(prefix="/api/v1/decisions")


@router.post("/evaluate")
async def evaluate(plan_id: int, user=Depends(require_operator)):
    """评估修复计划决策（四维风险评分）"""
    try:
        result = await evaluate_plan(plan_id)
        return {"code": 200, "message": "success", "data": result}
    except ValueError as e:
        return {"code": 400, "message": str(e), "data": None}


@router.get("/risk-policy")
async def get_risk_policy_api(user=Depends(require_admin)):
    """读风险评分策略（含默认值）"""
    from app.services.decision_engine import get_risk_policy
    policy = await get_risk_policy()
    return {"code": 200, "message": "success", "data": policy}


@router.put("/risk-policy")
async def update_risk_policy_api(payload: dict, user=Depends(require_admin)):
    """更新风险评分策略（JSON 覆盖到 system_settings）"""
    import json as _json
    from app.services.system_setting_service import update_settings
    await update_settings({"risk_policy": _json.dumps(payload, ensure_ascii=False)})
    return {"code": 200, "message": "success", "data": None}


@router.get("/policies")
async def list_policies(user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(select(DecisionPolicy))
        policies = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": p.id, "name": p.name, "schedule_type": p.schedule_type, "days_of_week": p.days_of_week,
         "start_time": p.start_time, "end_time": p.end_time, "read_rule": p.read_rule,
         "create_rule": p.create_rule, "update_rule": p.update_rule, "delete_rule": p.delete_rule,
         "auto_escalation": p.auto_escalation}
        for p in policies
    ]}


@router.post("/policies")
async def create_policy(payload: dict, user=Depends(require_admin)):
    async with async_session() as session:
        p = DecisionPolicy(**{k: v for k, v in payload.items() if k in (
            "name", "schedule_type", "days_of_week", "start_time", "end_time",
            "read_rule", "create_rule", "update_rule", "delete_rule", "auto_escalation")})
        session.add(p)
        await session.commit()
        await session.refresh(p)
        return {"code": 200, "message": "success", "data": {"id": p.id}}


@router.put("/policies/{policy_id}")
async def update_policy(policy_id: int, payload: dict, user=Depends(require_admin)):
    async with async_session() as session:
        p = await session.get(DecisionPolicy, policy_id)
        if not p:
            return {"code": 404, "message": "策略不存在", "data": None}
        editable = ("name", "schedule_type", "days_of_week", "start_time", "end_time",
                    "read_rule", "create_rule", "update_rule", "delete_rule", "auto_escalation")
        for k in editable:
            if k in payload:
                setattr(p, k, payload[k])
        await session.commit()
    return {"code": 200, "message": "success", "data": None}
