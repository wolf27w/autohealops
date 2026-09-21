# -*- coding: utf-8 -*-
"""容器资源阈值告警规则 API"""
import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.middleware.permission import require_admin, require_any_user
from app.models import ResourceAlertRule
from app.services import resource_alert_service

logger = get_logger("api-server.resource_alert_rules")

router = APIRouter(prefix="/api/v1/resource-alert-rules")


async def _scan_new_rule(rule_id: int) -> None:
    """新增规则后立即扫一次（不等下一个周期，秒级生效）"""
    try:
        async with async_session() as session:
            r = await session.get(ResourceAlertRule, rule_id)
        if r:
            await resource_alert_service._scan_rule(r)
    except Exception as e:  # noqa: BLE001
        logger.warning("新规则 %s 立即扫描失败: %s", rule_id, e)


def _rule_dict(r: ResourceAlertRule) -> dict:
    return {
        "id": r.id, "name": r.name, "cluster_id": r.cluster_id,
        "namespace_match": r.namespace_match, "container_match": r.container_match,
        "metric_type": r.metric_type, "threshold_pct": r.threshold_pct,
        "duration_seconds": r.duration_seconds, "severity": r.severity,
        "enabled": r.enabled, "created_at": str(r.created_at),
    }


@router.get("")
async def list_rules(user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(select(ResourceAlertRule).order_by(ResourceAlertRule.id.desc()))
        rules = list(result.scalars())
    return {"code": 200, "message": "success", "data": [_rule_dict(r) for r in rules]}


@router.post("")
async def create_rule(payload: dict, user=Depends(require_admin)):
    name = payload.get("name", "").strip()
    metric_type = payload.get("metric_type", "memory")
    if not name or not metric_type:
        return {"code": 400, "message": "名称和 metric_type 必填", "data": None}
    async with async_session() as session:
        r = ResourceAlertRule(
            name=name,
            cluster_id=int(payload.get("cluster_id", 0) or 0),
            namespace_match=payload.get("namespace_match", "").strip(),
            container_match=payload.get("container_match", "").strip(),
            metric_type=metric_type,
            threshold_pct=float(payload.get("threshold_pct", 80)),
            duration_seconds=int(payload.get("duration_seconds", 300)),
            severity=payload.get("severity", "medium"),
            enabled=payload.get("enabled", True),
        )
        session.add(r)
        await session.commit()
        await session.refresh(r)
        # 立即扫一次（秒级生效，不用等下一个扫描周期）
        asyncio.create_task(_scan_new_rule(r.id))
        return {"code": 200, "message": "success", "data": _rule_dict(r)}


@router.put("/{rule_id}")
async def update_rule(rule_id: int, payload: dict, user=Depends(require_admin)):
    async with async_session() as session:
        r = await session.get(ResourceAlertRule, rule_id)
        if not r:
            return {"code": 404, "message": "规则不存在", "data": None}
        for field in ("name", "namespace_match", "container_match", "severity"):
            if field in payload:
                setattr(r, field, payload[field])
        if "metric_type" in payload and payload["metric_type"]:
            r.metric_type = payload["metric_type"]
        if "cluster_id" in payload:
            r.cluster_id = int(payload["cluster_id"] or 0)
        if "threshold_pct" in payload:
            r.threshold_pct = float(payload["threshold_pct"])
        if "duration_seconds" in payload:
            r.duration_seconds = int(payload["duration_seconds"])
        if "enabled" in payload:
            r.enabled = payload["enabled"]
        await session.commit()
        await session.refresh(r)
    return {"code": 200, "message": "success", "data": _rule_dict(r)}


@router.delete("/{rule_id}")
async def delete_rule(rule_id: int, user=Depends(require_admin)):
    """删除规则：顺带关闭其名下在途告警（删除即消失）"""
    async with async_session() as session:
        r = await session.get(ResourceAlertRule, rule_id)
        if not r:
            return {"code": 404, "message": "规则不存在", "data": None}
        closed = await resource_alert_service.close_alerts_for_rule(r)
        await session.delete(r)
        await session.commit()
    return {"code": 200, "message": "success", "data": {"closed_alerts": closed}}
