# -*- coding: utf-8 -*-
"""告警屏蔽 API（R11，Alertmanager 风格）"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_any_user, require_operator
from app.models import AlertSilence
from app.services.notification_service import silence_matches

router = APIRouter(prefix="/api/v1/alert-silences")


def _row(r: AlertSilence) -> dict:
    try:
        matchers = json.loads(r.matchers or "[]")
    except Exception:
        matchers = []
    return {
        "id": r.id, "name": r.name, "matchers": matchers,
        "starts_at": str(r.starts_at), "ends_at": str(r.ends_at),
        "comment": r.comment, "created_by": r.created_by,
        "created_at": str(r.created_at), "updated_at": str(r.updated_at),
    }


@router.get("")
async def list_silences(user=Depends(require_any_user)):
    async with async_session() as s:
        rows = (await s.execute(select(AlertSilence).order_by(AlertSilence.id.desc()))).scalars().all()
    return {"code": 200, "message": "success", "data": [_row(r) for r in rows]}


@router.post("")
async def create_silence(payload: dict, user=Depends(require_operator)):
    matchers = payload.get("matchers") or []
    starts_at = payload.get("starts_at")
    ends_at = payload.get("ends_at")
    if not ends_at:
        return {"code": 400, "message": "ends_at 必填", "data": None}
    try:
        starts = datetime.fromisoformat(starts_at) if starts_at else datetime.now()
        ends = datetime.fromisoformat(ends_at)
    except ValueError:
        return {"code": 400, "message": "时间格式非法（需 ISO8601）", "data": None}
    async with async_session() as s:
        row = AlertSilence(
            name=payload.get("name", ""),
            matchers=json.dumps(matchers, ensure_ascii=False),
            starts_at=starts, ends_at=ends,
            comment=payload.get("comment", ""),
            created_by=(getattr(user, "username", "") or ""),
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
        return {"code": 200, "message": "success", "data": _row(row)}


@router.delete("/{sid}")
async def delete_silence(sid: int, user=Depends(require_operator)):
    async with async_session() as s:
        row = await s.get(AlertSilence, sid)
        if not row:
            return {"code": 404, "message": "屏蔽条目不存在", "data": None}
        await s.delete(row)
        await s.commit()
    return {"code": 200, "message": "success", "data": {"id": sid}}


@router.post("/preview")
async def preview_silence(payload: dict, user=Depends(require_any_user)):
    """命中预览：给定 matchers + 一个告警字段样本，返回是否命中。"""
    matchers = payload.get("matchers") or []
    sample = payload.get("sample") or {}

    class _Sample:
        pass

    s = _Sample()
    s.cluster_id = sample.get("cluster_id")
    s.namespace = sample.get("namespace", "")
    s.resource_type = sample.get("resource_type", "")
    s.resource_name = sample.get("resource_name", "")
    s.fault_type = sample.get("fault_type", "")
    s.severity = sample.get("severity", "")
    matched = silence_matches(matchers, s)
    return {"code": 200, "message": "success", "data": {"matched": matched}}
