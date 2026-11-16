# -*- coding: utf-8 -*-
"""告警屏蔽 API（R11，Alertmanager 风格）：CRUD + 重复时间窗 + 生效状态 + 命中预览 + 抑制规则"""
import json
from datetime import datetime, timedelta, timedelta
from types import SimpleNamespace

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.core.database import async_session
from app.middleware.permission import require_any_user, require_operator
from app.models import Alert, AlertInhibition, AlertSilence
from app.services.alert_silence_service import (
    matcher_filters,
    silence_status,
    validate_matchers,
    validate_recurrence,
    window_state,
)

router = APIRouter(prefix="/api/v1/alert-silences")

PREVIEW_DAYS = 7


def _parse_dt(value):
    """ISO8601 字符串 → datetime；解析失败返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _row(r: AlertSilence, now: datetime | None = None) -> dict:
    try:
        matchers = json.loads(r.matchers or "[]")
    except Exception:
        matchers = []
    try:
        recurrence = json.loads(r.recurrence or "{}")
    except Exception:
        recurrence = {}
    return {
        "id": r.id, "name": r.name, "matchers": matchers,
        "starts_at": str(r.starts_at), "ends_at": str(r.ends_at),
        "recurrence": recurrence,
        "active": silence_status(r, now)["active"],
        "comment": r.comment, "created_by": r.created_by,
        "created_at": str(r.created_at), "updated_at": str(r.updated_at),
    }


@router.get("")
async def list_silences(user=Depends(require_any_user)):
    now = datetime.now()
    async with async_session() as s:
        rows = (await s.execute(select(AlertSilence).order_by(AlertSilence.id.desc()))).scalars().all()
    return {"code": 200, "message": "success", "data": [_row(r, now) for r in rows]}


@router.post("")
async def create_silence(payload: dict, user=Depends(require_operator)):
    matchers = payload.get("matchers") or []
    errors = validate_matchers(matchers)
    if errors:
        return {"code": 400, "message": "; ".join(errors), "data": None}
    recurrence = payload.get("recurrence") or {}
    rec_err = validate_recurrence(recurrence)
    if rec_err:
        return {"code": 400, "message": rec_err, "data": None}
    starts_at = payload.get("starts_at")
    ends_at = payload.get("ends_at")
    rec_type = (recurrence or {}).get("type") if isinstance(recurrence, dict) else None
    starts = _parse_dt(starts_at) if starts_at else datetime.now()
    if starts is None:
        return {"code": 400, "message": "时间格式非法（需 ISO8601）", "data": None}
    if rec_type in ("daily", "weekly"):
        # 周期屏蔽没有天然结束点：生效判定以 recurrence 为准，ends_at 只作外层上限（默认 10 年）
        ends = _parse_dt(ends_at) if ends_at else starts + timedelta(days=3650)
    else:
        if not ends_at:
            return {"code": 400, "message": "ends_at 必填（一次性屏蔽需指定结束时间）", "data": None}
        ends = _parse_dt(ends_at)
    if ends is None:
        return {"code": 400, "message": "时间格式非法（需 ISO8601）", "data": None}
    async with async_session() as s:
        row = AlertSilence(
            name=payload.get("name", ""),
            matchers=json.dumps(matchers, ensure_ascii=False),
            recurrence=json.dumps(recurrence, ensure_ascii=False),
            starts_at=starts, ends_at=ends,
            comment=payload.get("comment", ""),
            created_by=(getattr(user, "username", "") or ""),
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
        return {"code": 200, "message": "success", "data": _row(row)}


@router.get("/{sid}/status")
async def get_silence_status(sid: int, user=Depends(require_any_user)):
    async with async_session() as s:
        row = await s.get(AlertSilence, sid)
    if not row:
        return {"code": 404, "message": "屏蔽条目不存在", "data": None}
    return {"code": 200, "message": "success", "data": silence_status(row)}


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
    """命中预览：近 7 天 alerts 按 matcher 过滤做只读聚合（不把行拉到 Python）。"""
    matchers = payload.get("matchers") or []
    errors = validate_matchers(matchers)
    if errors:
        return {"code": 400, "message": "; ".join(errors), "data": None}
    recurrence = payload.get("recurrence")
    if recurrence is not None:
        rec_err = validate_recurrence(recurrence)
        if rec_err:
            return {"code": 400, "message": rec_err, "data": None}

    sl = SimpleNamespace(
        recurrence=recurrence if recurrence is not None else "{}",
        starts_at=_parse_dt(payload.get("starts_at")),
        ends_at=_parse_dt(payload.get("ends_at")),
    )
    ws = window_state(sl, datetime.now())
    window = {
        "start": str(ws.get("start") or ws.get("next_start")) if (ws.get("start") or ws.get("next_start")) else None,
        "end": str(ws.get("end") or ws.get("next_end")) if (ws.get("end") or ws.get("next_end")) else None,
        "active": ws["active"],
    }

    since = datetime.now() - timedelta(days=PREVIEW_DAYS)
    conds = matcher_filters(Alert, matchers)
    conds.append(Alert.detected_at >= since)
    async with async_session() as s:
        total = (await s.execute(select(func.count()).select_from(Alert).where(*conds))).scalar() or 0
        by_fault = (await s.execute(
            select(Alert.fault_type, func.count()).where(*conds)
            .group_by(Alert.fault_type).order_by(func.count().desc())
        )).all()
        samples = (await s.execute(
            select(Alert).where(*conds).order_by(Alert.id.desc()).limit(5)
        )).scalars().all()
    return {
        "code": 200, "message": "success",
        "data": {
            "matched_total": total,
            "by_fault_type": {ft: c for ft, c in by_fault},
            "samples": [_sample_alert(a) for a in samples],
            "window": window,
        },
    }


def _sample_alert(a: Alert) -> dict:
    return {
        "id": a.id,
        "cluster_id": a.cluster_id,
        "namespace": a.namespace,
        "resource_type": a.resource_type,
        "resource_name": a.resource_name,
        "fault_type": a.fault_type,
        "severity": a.severity,
        "status": a.status,
        "detected_at": str(a.detected_at),
    }


# ============ 抑制规则（alert_inhibitions） ============

def _inhibition_row(r: AlertInhibition) -> dict:
    try:
        src = json.loads(r.source_matchers or "[]")
    except Exception:
        src = []
    try:
        tgt = json.loads(r.target_matchers or "[]")
    except Exception:
        tgt = []
    try:
        eq = json.loads(r.equal_labels or "[]")
    except Exception:
        eq = []
    return {
        "id": r.id, "name": r.name,
        "source_matchers": src, "target_matchers": tgt, "equal_labels": eq,
        "enabled": bool(r.enabled), "comment": r.comment, "created_by": r.created_by,
        "created_at": str(r.created_at), "updated_at": str(r.updated_at),
    }


def _json_list(raw: str) -> list:
    """健壮解析 JSON 数组字段（脏数据回退空列表）。"""
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


def _validate_inhibition(payload: dict) -> str | None:
    src = payload.get("source_matchers") or []
    tgt = payload.get("target_matchers") or []
    if not src or not tgt:
        return "source_matchers / target_matchers 均必填"
    for label, ms in (("source_matchers", src), ("target_matchers", tgt)):
        errs = validate_matchers(ms)
        if errs:
            return f"{label}: " + "; ".join(errs)
    eq = payload.get("equal_labels") or []
    if not isinstance(eq, list):
        return "equal_labels 必须是数组"
    return None


@router.get("/inhibitions")
async def list_inhibitions(user=Depends(require_any_user)):
    async with async_session() as s:
        rows = (await s.execute(select(AlertInhibition).order_by(AlertInhibition.id.desc()))).scalars().all()
    return {"code": 200, "message": "success", "data": [_inhibition_row(r) for r in rows]}


@router.post("/inhibitions")
async def create_inhibition(payload: dict, user=Depends(require_operator)):
    err = _validate_inhibition(payload)
    if err:
        return {"code": 400, "message": err, "data": None}
    async with async_session() as s:
        row = AlertInhibition(
            name=payload.get("name", ""),
            source_matchers=json.dumps(payload.get("source_matchers") or [], ensure_ascii=False),
            target_matchers=json.dumps(payload.get("target_matchers") or [], ensure_ascii=False),
            equal_labels=json.dumps(payload.get("equal_labels") or [], ensure_ascii=False),
            enabled=bool(payload.get("enabled", True)),
            comment=payload.get("comment", ""),
            created_by=(getattr(user, "username", "") or ""),
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
        return {"code": 200, "message": "success", "data": _inhibition_row(row)}


@router.put("/inhibitions/{iid}")
async def update_inhibition(iid: int, payload: dict, user=Depends(require_operator)):
    """更新抑制规则：支持部分字段（例如只切 enabled），未传的字段沿用原值。"""
    async with async_session() as s:
        row = await s.get(AlertInhibition, iid)
        if not row:
            return {"code": 404, "message": "抑制规则不存在", "data": None}
        merged = {
            "name": payload.get("name", row.name),
            "source_matchers": payload.get("source_matchers", _json_list(row.source_matchers)),
            "target_matchers": payload.get("target_matchers", _json_list(row.target_matchers)),
            "equal_labels": payload.get("equal_labels", _json_list(row.equal_labels)),
            "enabled": payload.get("enabled", row.enabled),
            "comment": payload.get("comment", row.comment),
        }
        err = _validate_inhibition(merged)
        if err:
            return {"code": 400, "message": err, "data": None}
        row.name = merged["name"]
        row.source_matchers = json.dumps(merged["source_matchers"] or [], ensure_ascii=False)
        row.target_matchers = json.dumps(merged["target_matchers"] or [], ensure_ascii=False)
        row.equal_labels = json.dumps(merged["equal_labels"] or [], ensure_ascii=False)
        row.enabled = bool(merged["enabled"])
        row.comment = merged["comment"]
        await s.commit()
        await s.refresh(row)
        return {"code": 200, "message": "success", "data": _inhibition_row(row)}


@router.delete("/inhibitions/{iid}")
async def delete_inhibition(iid: int, user=Depends(require_operator)):
    async with async_session() as s:
        row = await s.get(AlertInhibition, iid)
        if not row:
            return {"code": 404, "message": "抑制规则不存在", "data": None}
        await s.delete(row)
        await s.commit()
    return {"code": 200, "message": "success", "data": {"id": iid}}
