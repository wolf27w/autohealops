# -*- coding: utf-8 -*-
"""日志告警规则 API（CRUD）"""
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_admin, require_any_user
from app.models import LogAlertRule

router = APIRouter(prefix="/api/v1/log-alert-rules")


def _to_dict(r: LogAlertRule) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "cluster_id": r.cluster_id,
        "index_pattern": r.index_pattern,
        "log_source_id": r.log_source_id,
        "keywords": r.keywords,
        "namespace_filter": r.namespace_filter,
        "time_window": r.time_window,
        "threshold": r.threshold,
        "severity": r.severity,
        "enabled": r.enabled,
        "last_triggered_at": str(r.last_triggered_at) if r.last_triggered_at else None,
    }


@router.get("")
async def list_rules(user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(select(LogAlertRule).order_by(LogAlertRule.id.desc()))
        rules = result.scalars().all()
    return {"code": 200, "message": "success", "data": [_to_dict(r) for r in rules]}


@router.post("")
async def create_rule(payload: dict, user=Depends(require_admin)):
    async with async_session() as session:
        r = LogAlertRule(
            name=payload.get("name", ""),
            cluster_id=payload.get("cluster_id", 0),
            index_pattern=payload.get("index_pattern", ""),
            log_source_id=payload.get("log_source_id") or None,
            keywords=payload.get("keywords", "[]"),
            namespace_filter=payload.get("namespace_filter", ""),
            time_window=payload.get("time_window", 5),
            threshold=payload.get("threshold", 3),
            severity=payload.get("severity", "medium"),
            enabled=payload.get("enabled", True),
        )
        session.add(r)
        await session.commit()
        await session.refresh(r)
    return {"code": 200, "message": "success", "data": _to_dict(r)}


@router.put("/{rule_id}")
async def update_rule(rule_id: int, payload: dict, user=Depends(require_admin)):
    async with async_session() as session:
        r = await session.get(LogAlertRule, rule_id)
        if not r:
            return {"code": 404, "message": "规则不存在", "data": None}
        for field in ("name", "cluster_id", "index_pattern", "keywords", "namespace_filter",
                      "time_window", "threshold", "severity", "enabled"):
            if field in payload:
                setattr(r, field, payload[field])
        if "log_source_id" in payload:
            r.log_source_id = payload.get("log_source_id") or None
        await session.commit()
        await session.refresh(r)
    return {"code": 200, "message": "success", "data": _to_dict(r)}


@router.delete("/{rule_id}")
async def delete_rule(rule_id: int, user=Depends(require_admin)):
    async with async_session() as session:
        r = await session.get(LogAlertRule, rule_id)
        if not r:
            return {"code": 404, "message": "规则不存在", "data": None}
        await session.delete(r)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}
