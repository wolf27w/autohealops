# -*- coding: utf-8 -*-
"""报表定时推送 API（C4）：CRUD + 立即推送 + 预览"""
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_admin, require_any_user
from app.models import ReportSchedule
from app.services import report_service

router = APIRouter(prefix="/api/v1/report-schedules")

_EDITABLE = ("name", "cron_expression", "notify_config_id", "range_days", "enabled")


def _to_dict(sc: ReportSchedule) -> dict:
    return {
        "id": sc.id, "name": sc.name, "cron_expression": sc.cron_expression,
        "notify_config_id": sc.notify_config_id, "range_days": sc.range_days,
        "enabled": bool(sc.enabled),
        "last_run_at": str(sc.last_run_at) if sc.last_run_at else "",
        "created_at": str(sc.created_at),
    }


@router.get("")
async def list_schedules(user=Depends(require_any_user)):
    async with async_session() as session:
        rows = list((await session.execute(select(ReportSchedule).order_by(ReportSchedule.id.desc()))).scalars())
    return {"code": 200, "message": "success", "data": [_to_dict(sc) for sc in rows]}


@router.get("/preview")
async def preview(range_days: int = 7, user=Depends(require_any_user)):
    """预览报表文本"""
    return {"code": 200, "message": "success", "data": {"text": await report_service.build_report(range_days)}}


@router.post("")
async def create_schedule(payload: dict, user=Depends(require_admin)):
    name = (payload.get("name") or "").strip()
    cron = (payload.get("cron_expression") or "").strip()
    if not name or not cron:
        return {"code": 400, "message": "name 与 cron_expression 必填", "data": None}
    async with async_session() as session:
        sc = ReportSchedule(
            name=name, cron_expression=cron,
            notify_config_id=payload.get("notify_config_id") or None,
            range_days=int(payload.get("range_days") or 7),
            enabled=bool(payload.get("enabled", True)),
        )
        session.add(sc)
        await session.commit()
        await session.refresh(sc)
    report_service.register_schedule(sc)
    return {"code": 200, "message": "success", "data": {"id": sc.id}}


@router.put("/{schedule_id}")
async def update_schedule(schedule_id: int, payload: dict, user=Depends(require_admin)):
    async with async_session() as session:
        sc = await session.get(ReportSchedule, schedule_id)
        if not sc:
            return {"code": 404, "message": "任务不存在", "data": None}
        for k in _EDITABLE:
            if k in payload:
                v = payload[k]
                if k == "range_days":
                    v = int(v or 7)
                elif k == "notify_config_id":
                    v = v or None
                elif k == "enabled":
                    v = bool(v)
                setattr(sc, k, v)
        await session.commit()
        await session.refresh(sc)
    report_service.register_schedule(sc)
    return {"code": 200, "message": "success", "data": _to_dict(sc)}


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: int, user=Depends(require_admin)):
    report_service.unregister_schedule(schedule_id)
    async with async_session() as session:
        sc = await session.get(ReportSchedule, schedule_id)
        if sc:
            await session.delete(sc)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.post("/{schedule_id}/run")
async def run_now(schedule_id: int, user=Depends(require_admin)):
    """立即推送一次（测试用）"""
    r = await report_service.send_report(schedule_id)
    return {"code": 200 if r.get("ok") else 400, "message": "success" if r.get("ok") else r.get("reason", "失败"), "data": r}
