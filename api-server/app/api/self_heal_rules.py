# -*- coding: utf-8 -*-
"""自愈场景规则 API：强制人工/强制自动覆盖 + 影子模式"""
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_admin, require_any_user
from app.models import Cluster, SelfHealRule

router = APIRouter(prefix="/api/v1/self-heal-rules")


async def _to_dict(r: SelfHealRule) -> dict:
    cluster_name = ""
    if r.cluster_id:
        async with async_session() as session:
            c = await session.get(Cluster, r.cluster_id)
            if c:
                cluster_name = c.name
    return {
        "id": r.id, "name": r.name, "cluster_id": r.cluster_id, "cluster_name": cluster_name,
        "namespace_match": r.namespace_match, "fault_type_match": r.fault_type_match,
        "resource_type_match": r.resource_type_match, "action": r.action,
        "priority": r.priority, "shadow": r.shadow, "enabled": r.enabled,
    }


@router.get("")
async def list_rules(user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(select(SelfHealRule).order_by(SelfHealRule.priority.asc(), SelfHealRule.id.desc()))
        rules = list(result.scalars())
    data = []
    for r in rules:
        data.append(await _to_dict(r))
    return {"code": 200, "message": "success", "data": data}


@router.post("")
async def create_rule(payload: dict, user=Depends(require_admin)):
    name = (payload.get("name") or "").strip()
    action = payload.get("action", "")
    if not name or action not in ("force_manual", "force_auto"):
        return {"code": 400, "message": "名称必填，action 必须是 force_manual/force_auto", "data": None}
    async with async_session() as session:
        r = SelfHealRule(
            name=name,
            cluster_id=payload.get("cluster_id") or None,
            namespace_match=(payload.get("namespace_match") or "").strip(),
            fault_type_match=(payload.get("fault_type_match") or "").strip(),
            resource_type_match=(payload.get("resource_type_match") or "").strip(),
            action=action,
            priority=int(payload.get("priority", 100)),
            shadow=bool(payload.get("shadow", True)),
            enabled=bool(payload.get("enabled", True)),
        )
        session.add(r)
        await session.commit()
        await session.refresh(r)
    return {"code": 200, "message": "success", "data": await _to_dict(r)}


@router.put("/{rule_id}")
async def update_rule(rule_id: int, payload: dict, user=Depends(require_admin)):
    async with async_session() as session:
        r = await session.get(SelfHealRule, rule_id)
        if not r:
            return {"code": 404, "message": "规则不存在", "data": None}
        for f in ("name", "namespace_match", "fault_type_match", "resource_type_match", "action", "enabled"):
            if f in payload:
                setattr(r, f, payload[f])
        if "cluster_id" in payload:
            r.cluster_id = payload["cluster_id"] or None
        if "priority" in payload:
            r.priority = int(payload["priority"])
        if "shadow" in payload:
            r.shadow = bool(payload["shadow"])
        await session.commit()
        await session.refresh(r)
    return {"code": 200, "message": "success", "data": await _to_dict(r)}


@router.delete("/{rule_id}")
async def delete_rule(rule_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        r = await session.get(SelfHealRule, rule_id)
        if not r:
            return {"code": 404, "message": "规则不存在", "data": None}
        await session.delete(r)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}
