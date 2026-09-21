# -*- coding: utf-8 -*-
"""故障诊断 API（针对告警的 AI 诊断）

诊断分三类，本模块只负责「故障诊断」（告警）：
- 故障诊断      → 本模块（告警的 AI 诊断）
- 应用程序诊断  → AI 诊断助手（/agent，K8s Pod 会话式诊断）
- 数据库诊断    → 诊断中心（/diagnostic，数据库）
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select, func

from app.core.database import async_session
from app.middleware.permission import require_any_user, require_operator
from app.models import AIDiagnosisResult, AiFeedback, Alert, Cluster

router = APIRouter(prefix="/api/v1/fault-diagnoses")


@router.get("")
async def list_fault_diagnoses(page: int = 1, page_size: int = 50, category: str = "",
                              with_total: bool = False, user=Depends(require_any_user)):
    """列出告警及其诊断状态（故障诊断列表）

    category（N3 诊断分离）：cluster=集群故障 / app=应用故障 / db=数据库故障；空=全部。
    按 alerts.diag_category（STORED 生成列）过滤，三类互不串。
    """
    if category not in ("", "cluster", "app", "db"):
        return {"code": 400, "message": "category 仅支持 cluster/app/db", "data": None}
    # 分页参数兜底（防 page/page_size 非法或过大拖库）
    page = max(1, int(page or 1))
    page_size = min(max(1, int(page_size or 50)), 500)
    async with async_session() as session:
        stmt = select(Alert)
        if category:
            stmt = stmt.where(Alert.diag_category == category)
        total = 0
        if with_total:
            cnt_stmt = select(func.count()).select_from(Alert)
            if category:
                cnt_stmt = cnt_stmt.where(Alert.diag_category == category)
            total = int((await session.execute(cnt_stmt)).scalar() or 0)
        alerts = list((await session.execute(
            stmt.order_by(Alert.detected_at.desc(), Alert.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )).scalars())
        alert_ids = [a.id for a in alerts]
        diags: dict[int, AIDiagnosisResult] = {}
        if alert_ids:
            drows = (await session.execute(
                select(AIDiagnosisResult).where(AIDiagnosisResult.alert_id.in_(alert_ids))
                .order_by(AIDiagnosisResult.id.desc())
            )).scalars().all()
            for d in drows:
                if d.alert_id not in diags:
                    diags[d.alert_id] = d  # 保留最新
        cluster_names = {c.id: c.name for c in (await session.execute(select(Cluster))).scalars()}
    data = []
    for a in alerts:
        d = diags.get(a.id)
        root_cause = ""
        if d and d.diagnosis_json:
            try:
                import json as _json
                root_cause = (str(_json.loads(d.diagnosis_json).get("root_cause", "")))[:120]
            except Exception:  # noqa: BLE001
                root_cause = ""
        data.append({
            "alert_id": a.id, "cluster_id": a.cluster_id, "cluster_name": cluster_names.get(a.cluster_id, ""),
            "resource_type": a.resource_type, "resource_name": a.resource_name, "namespace": a.namespace,
            "fault_type": a.fault_type, "severity": a.severity, "status": a.status,
            "diag_category": a.diag_category,
            "message": (a.message or "")[:200], "detected_at": str(a.detected_at),
            "has_diagnosis": d is not None,
            "confidence": d.confidence if d else None,
            "diagnosis_time": str(d.created_at) if d else "",
            "root_cause": root_cause,
        })
    if with_total:
        return {"code": 200, "message": "success", "data": {"items": data, "total": total, "page": page, "page_size": page_size}}
    return {"code": 200, "message": "success", "data": data}


@router.get("/{alert_id}/feedback")
async def get_feedback(alert_id: int, user=Depends(require_any_user)):
    """AI 诊断反馈（C5）：列表 + 有用/无用统计"""
    async with async_session() as session:
        rows = list((await session.execute(
            select(AiFeedback).where(AiFeedback.alert_id == alert_id).order_by(AiFeedback.id.desc())
        )).scalars())
    useful = sum(1 for r in rows if r.rating > 0)
    useless = sum(1 for r in rows if r.rating < 0)
    return {"code": 200, "message": "success", "data": {
        "useful": useful, "useless": useless,
        "items": [{"id": r.id, "rating": r.rating, "comment": r.comment, "username": r.username,
                   "created_at": str(r.created_at)} for r in rows],
    }}


@router.post("/{alert_id}/feedback")
async def add_feedback(alert_id: int, payload: dict, user=Depends(require_operator)):
    """提交 AI 结果反馈：rating=1 有用 / -1 无用"""
    try:
        rating = int(payload.get("rating", 0))
    except (TypeError, ValueError):
        rating = 0
    if rating not in (1, -1):
        return {"code": 400, "message": "rating 必须是 1（有用）或 -1（无用）", "data": None}
    async with async_session() as session:
        fb = AiFeedback(
            alert_id=alert_id, rating=rating,
            comment=(payload.get("comment") or "")[:500],
            username=user.username if user else "",
        )
        session.add(fb)
        await session.commit()
        await session.refresh(fb)
    return {"code": 200, "message": "success", "data": {"id": fb.id}}


@router.get("/{alert_id}")
async def get_fault_diagnosis(alert_id: int, user=Depends(require_any_user)):
    """查看某告警的最新诊断结果"""
    async with async_session() as session:
        d = (await session.execute(
            select(AIDiagnosisResult).where(AIDiagnosisResult.alert_id == alert_id)
            .order_by(AIDiagnosisResult.id.desc()).limit(1)
        )).scalar_one_or_none()
    if not d:
        return {"code": 404, "message": "无诊断结果", "data": None}
    return {"code": 200, "message": "success", "data": {
        "diagnosis_json": d.diagnosis_json, "confidence": d.confidence, "created_at": str(d.created_at),
    }}
