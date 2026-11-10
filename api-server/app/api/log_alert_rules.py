# -*- coding: utf-8 -*-
"""日志告警规则 API（CRUD）"""
import json

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_admin, require_any_user
from app.models import LogAlertRule
from app.services import log_rule_presets

router = APIRouter(prefix="/api/v1/log-alert-rules")


def _to_dict(r: LogAlertRule) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "cluster_id": r.cluster_id,
        "index_pattern": r.index_pattern,
        "log_source_id": r.log_source_id,
        "notify_config_id": r.notify_config_id,
        "keywords": r.keywords,
        "fault_type": getattr(r, "fault_type", "LogKeyword") or "LogKeyword",
        "category": getattr(r, "category", "app") or "app",
        "priority": getattr(r, "priority", 100) or 100,
        "sample_pattern": getattr(r, "sample_pattern", "") or "",
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
            notify_config_id=payload.get("notify_config_id") or None,
            keywords=payload.get("keywords", "[]"),
            fault_type=payload.get("fault_type", "LogKeyword"),
            category=payload.get("category", "app"),
            priority=payload.get("priority", 100),
            sample_pattern=payload.get("sample_pattern", ""),
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
        for field in ("name", "cluster_id", "index_pattern", "keywords", "fault_type", "category",
                      "priority", "sample_pattern", "namespace_filter",
                      "time_window", "threshold", "severity", "enabled"):
            if field in payload:
                setattr(r, field, payload[field])
        if "log_source_id" in payload:
            r.log_source_id = payload.get("log_source_id") or None
        if "notify_config_id" in payload:
            r.notify_config_id = payload.get("notify_config_id") or None
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


@router.post("/import-builtin")
async def import_builtin(payload: dict, user=Depends(require_admin)):
    """一键导入内置应用故障分类规则（按语言栈：java/go/node/python/all；按名称幂等）。"""
    lang = (payload.get("lang") or "all").strip().lower()
    if lang not in ("java", "go", "node", "python", "all"):
        return {"code": 400, "message": "lang 仅支持 java/go/node/python/all", "data": None}
    presets = log_rule_presets.presets_for(lang)
    created, skipped = 0, 0
    async with async_session() as session:
        existing = {r.name for r in (await session.execute(select(LogAlertRule))).scalars()}
        for pre in presets:
            if pre["name"] in existing:
                skipped += 1
                continue
            session.add(LogAlertRule(
                name=pre["name"], index_pattern=pre["index_pattern"],
                keywords=json.dumps(pre["keywords"], ensure_ascii=False),
                fault_type=pre["fault_type"], category=pre["category"], priority=pre["priority"],
                sample_pattern=pre["sample_pattern"], severity=pre["severity"],
                time_window=pre["time_window"], threshold=pre["threshold"],
            ))
            created += 1
        await session.commit()
    return {"code": 200, "message": "success",
            "data": {"created": created, "skipped": skipped, "lang": lang, "total": len(presets)}}
