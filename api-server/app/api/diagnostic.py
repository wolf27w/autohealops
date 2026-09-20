# -*- coding: utf-8 -*-
"""诊断中心 API：诊断任务 CRUD + 报告查询 + 手动触发"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_any_user
from app.models import DiagnosticReport, DiagnosticTask
from app.services import diagnostic_service, user_permission_service

router = APIRouter(prefix="/api/v1")


@router.post("/diagnostic-tasks")
async def create_task(payload: dict, user=Depends(require_any_user)):
    """创建诊断任务（应用：cluster+ns+pod；数据库：db_connection_id）"""
    task_type = payload.get("task_type", "app")
    if task_type not in ("app", "db"):
        return {"code": 400, "message": "任务类型仅支持 app/db", "data": None}
    target = payload.get("target", {})
    # 应用诊断：校验集群权限
    if task_type == "app":
        cluster_id = target.get("cluster_id")
        ns = target.get("namespace", "")
        if not cluster_id or not await user_permission_service.check_cluster_permission(user, cluster_id, ns):
            return {"code": 403, "message": "无该集群/命名空间权限", "data": None}
    async with async_session() as session:
        t = DiagnosticTask(
            task_type=task_type,
            name=payload.get("name", ""),
            target=json.dumps(target, ensure_ascii=False),
            time_window_minutes=int(payload.get("time_window_minutes", 60)),
            cron=payload.get("cron", ""),
            model_config_id=payload.get("model_config_id"),
            status="pending",
            created_by=user.id,
        )
        session.add(t)
        await session.commit()
        await session.refresh(t)
        # 无 cron = 立即执行一次；有 cron = 注册定时调度
        if t.cron:
            diagnostic_service.register_diagnostic_cron(t)
        else:
            diagnostic_service.schedule_run(t.id)
        return {"code": 200, "message": "success", "data": {"id": t.id}}


@router.get("/diagnostic-tasks")
async def list_tasks(user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(
            select(DiagnosticTask).where(DiagnosticTask.created_by == user.id).order_by(DiagnosticTask.id.desc())
            if user.role != "admin" else select(DiagnosticTask).order_by(DiagnosticTask.id.desc())
        )
        tasks = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": t.id, "task_type": t.task_type, "name": t.name, "target": t.target,
         "time_window_minutes": t.time_window_minutes, "cron": t.cron,
         "model_config_id": t.model_config_id, "status": t.status,
         "last_run_at": str(t.last_run_at) if t.last_run_at else "",
         "created_at": str(t.created_at)} for t in tasks
    ]}


@router.delete("/diagnostic-tasks/{task_id}")
async def delete_task(task_id: int, user=Depends(require_any_user)):
    async with async_session() as session:
        t = await session.get(DiagnosticTask, task_id)
        if t and (t.created_by == user.id or user.role == "admin"):
            diagnostic_service.unregister_diagnostic_cron(task_id)
            await session.delete(t)
            await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.post("/diagnostic-tasks/{task_id}/run")
async def run_task(task_id: int, user=Depends(require_any_user)):
    """手动触发诊断任务"""
    diagnostic_service.schedule_run(task_id)
    return {"code": 200, "message": "success", "data": None}


@router.get("/diagnostic-reports")
async def list_reports(task_id: int | None = None, user=Depends(require_any_user)):
    async with async_session() as session:
        stmt = select(DiagnosticReport).order_by(DiagnosticReport.id.desc())
        if task_id:
            stmt = stmt.where(DiagnosticReport.task_id == task_id)
        result = await session.execute(stmt.limit(100))
        reports = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": r.id, "task_id": r.task_id, "task_type": r.task_type,
         "conclusion": r.conclusion, "root_cause": r.root_cause, "suggestion": r.suggestion,
         "raw_output": r.raw_output[:2000], "created_at": str(r.created_at)} for r in reports
    ]}
