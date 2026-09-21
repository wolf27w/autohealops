# -*- coding: utf-8 -*-
"""数据库诊断 API：按需诊断 + 报告 + 趋势（诊断中心改造后入口）"""
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select, text

from app.core.database import async_session
from app.middleware.permission import require_any_user
from app.models import (
    DBMetricSnapshot,
    DiagnosticReport,
    ProjectDBConnection,
    UserDBConnection,
    UserProjectPermission,
)
from app.services.user_permission_service import check_db_permission

router = APIRouter(prefix="/api/v1/db-diagnosis")


def _deny():
    return {"code": 403, "message": "无权限访问该数据库连接", "data": None}


async def _visible_db_ids(user) -> set[int] | None:
    """用户可见的数据库连接 id 集合；admin 返回 None（表示全部）。

    可见连接 = 直接授权 ∪ 项目授权关联的连接（与 user_permission_service.check_db_permission 口径一致）。
    """
    if user.role == "admin":
        return None
    async with async_session() as session:
        direct = (await session.execute(
            select(UserDBConnection.db_connection_id)
            .where(UserDBConnection.user_id == user.id).distinct()
        )).scalars().all()
        project = (await session.execute(
            select(ProjectDBConnection.db_connection_id)
            .join(UserProjectPermission, UserProjectPermission.project_id == ProjectDBConnection.project_id)
            .where(UserProjectPermission.user_id == user.id).distinct()
        )).scalars().all()
    return set(direct) | set(project)


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

    # conn_id 过滤（P1 越权修复）：admin 全量；单库用户只能看自己有权限的连接报告。
    # conn_id 列为迁移 q0r1s2t3u4v5 新增（ORM 模型暂未映射该列），故用 text() 条件过滤；
    # 值均为 int，直接内插无注入风险。IN 列表天然排除 conn_id IS NULL 的历史报告（不展示给单库用户）。
    cond = "task_type = 'db'"
    if conn_id is not None:
        cond += f" AND conn_id = {int(conn_id)}"
    else:
        visible = await _visible_db_ids(user)
        if visible is not None:
            if not visible:
                return {"code": 200, "message": "success", "data": {
                    "items": [], "total": 0, "page": page, "page_size": page_size}}
            cond += f" AND conn_id IN ({','.join(str(i) for i in visible)})"

    from sqlalchemy import func as _func
    async with async_session() as session:
        total = (await session.execute(
            select(_func.count()).select_from(DiagnosticReport).where(text(cond))
        )).scalar() or 0
        rows = (await session.execute(
            select(DiagnosticReport).where(text(cond))
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
async def list_trends(conn_id: int, metric_type: str = "connections", hours: int = 1, user=Depends(require_any_user)):
    """指标趋势；metric_type=all 时返回全量指标（按时间窗，前端多线展示）"""
    if not await check_db_permission(user, conn_id, "read"):
        return _deny()
    async with async_session() as session:
        if metric_type in ("", "all"):
            since = datetime.now() - timedelta(hours=max(1, min(hours, 48)))
            rows = (await session.execute(
                select(DBMetricSnapshot)
                .where(DBMetricSnapshot.db_connection_id == conn_id,
                       DBMetricSnapshot.sampled_at >= since)
                .order_by(DBMetricSnapshot.id.desc()).limit(20000)
            )).scalars().all()
            rows = list(reversed(rows))
            return {"code": 200, "message": "success", "data": [
                {"metric_type": r.metric_type, "metric_name": r.metric_name,
                 "value": r.value, "sampled_at": str(r.sampled_at)} for r in rows
            ]}
        result = await session.execute(
            select(DBMetricSnapshot)
            .where(
                DBMetricSnapshot.db_connection_id == conn_id,
                DBMetricSnapshot.metric_type == metric_type,
            )
            .order_by(DBMetricSnapshot.sampled_at.desc())
            .limit(200)
        )
        rows2 = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"metric_name": r.metric_name, "value": r.value, "sampled_at": str(r.sampled_at)}
        for r in reversed(rows2)
    ]}
