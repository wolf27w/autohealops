# -*- coding: utf-8 -*-
"""数据库诊断 API：按需诊断 + 报告 + 趋势（诊断中心改造后入口）"""
import json

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_any_user
from app.models import DBMetricSnapshot, DiagnosticReport
from app.services.user_permission_service import check_db_permission

router = APIRouter(prefix="/api/v1/db-diagnosis")


def _deny():
    return {"code": 403, "message": "无权限访问该数据库连接", "data": None}


@router.post("/{conn_id}/run")
async def run_diagnosis(conn_id: int, user=Depends(require_any_user)):
    """对数据库连接执行按需诊断（采集→AI 分析→报告→沉淀知识库）"""
    if not await check_db_permission(user, conn_id, "read"):
        return _deny()
    from app.services.db_diagnosis_service import diagnose_db
    return await diagnose_db(conn_id, user)


@router.get("/reports")
async def list_reports(conn_id: int | None = None, page: int = 1, page_size: int = 20,
                      user=Depends(require_any_user)):
    if conn_id and not await check_db_permission(user, conn_id, "read"):
        return _deny()
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    from sqlalchemy import func as _func
    async with async_session() as session:
        total = (await session.execute(
            select(_func.count()).select_from(DiagnosticReport).where(DiagnosticReport.task_type == "db")
        )).scalar() or 0
        rows = (await session.execute(
            select(DiagnosticReport).where(DiagnosticReport.task_type == "db")
            .order_by(DiagnosticReport.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
        items = []
        for r in rows:
            db_name, db_type = "", ""
            try:
                raw = json.loads(r.raw_output or "{}")
                db_name, db_type = raw.get("db_name", ""), raw.get("db_type", "")
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass
            items.append({"id": r.id, "db_name": db_name, "db_type": db_type,
                          "conclusion": r.conclusion or "", "root_cause": r.root_cause or "",
                          "suggestion": r.suggestion or "", "created_at": str(r.created_at)})
    return {"code": 200, "message": "success", "data": {
        "items": items, "total": total, "page": page, "page_size": page_size,
    }}


@router.get("/{conn_id}/metrics")
async def latest_metrics(conn_id: int, user=Depends(require_any_user)):
    """最新一次采集的全部监控指标（按 metric_name 取最新）"""
    if not await check_db_permission(user, conn_id, "read"):
        return _deny()
    from sqlalchemy import func as _func
    async with async_session() as session:
        sub = (select(DBMetricSnapshot.metric_name, _func.max(DBMetricSnapshot.id).label("mid"))
               .where(DBMetricSnapshot.db_connection_id == conn_id)
               .group_by(DBMetricSnapshot.metric_name).subquery())
        rows = (await session.execute(
            select(DBMetricSnapshot).join(sub, sub.c.mid == DBMetricSnapshot.id)
            .order_by(DBMetricSnapshot.metric_type, DBMetricSnapshot.metric_name)
        )).scalars().all()
    return {"code": 200, "message": "success", "data": [
        {"metric_type": r.metric_type, "metric_name": r.metric_name,
         "value": r.value, "detail": r.detail or "", "sampled_at": str(r.sampled_at)} for r in rows
    ]}


@router.get("/{conn_id}/progress")
async def diag_progress(conn_id: int, user=Depends(require_any_user)):
    """诊断进度（前端轮询）：{pct, step}；idle=未在诊断"""
    if not await check_db_permission(user, conn_id, "read"):
        return _deny()
    from app.services.db_diagnosis_service import get_diag_progress
    return {"code": 200, "message": "success", "data": await get_diag_progress(conn_id)}


@router.get("/{conn_id}/trends")
async def list_trends(conn_id: int, metric_type: str = "connections", user=Depends(require_any_user)):
    """连接/锁/表空间趋势（db_metric_snapshots）"""
    if not await check_db_permission(user, conn_id, "read"):
        return _deny()
    async with async_session() as session:
        result = await session.execute(
            select(DBMetricSnapshot)
            .where(
                DBMetricSnapshot.db_connection_id == conn_id,
                DBMetricSnapshot.metric_type == metric_type,
            )
            .order_by(DBMetricSnapshot.sampled_at.desc())
            .limit(200)
        )
        rows = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"metric_name": r.metric_name, "value": r.value, "sampled_at": str(r.sampled_at)}
        for r in reversed(rows)
    ]}
