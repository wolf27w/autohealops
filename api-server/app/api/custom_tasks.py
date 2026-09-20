# -*- coding: utf-8 -*-
"""定时任务 API"""
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_admin, require_any_user, require_operator
from app.models import CustomTask, CustomTaskLog
from app.services import scheduler_service

router = APIRouter(prefix="/api/v1/custom-tasks")


@router.get("")
async def list_tasks(user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(select(CustomTask).order_by(CustomTask.id.desc()))
        tasks = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": t.id, "cluster_id": t.cluster_id, "name": t.name, "task_type": t.task_type,
         "target_resource": t.target_resource, "target_namespace": t.target_namespace,
         "cron_expression": t.cron_expression, "is_enabled": t.is_enabled,
         "last_run_status": t.last_run_status, "last_run_at": t.last_run_at}
        for t in tasks
    ]}


@router.post("")
async def create_task(payload: dict, user=Depends(require_operator)):
    async with async_session() as session:
        t = CustomTask(**{k: v for k, v in payload.items() if k in (
            "cluster_id", "name", "task_type", "target_resource", "target_namespace",
            "cron_expression", "script_content", "parameters")})
        session.add(t)
        await session.commit()
        await session.refresh(t)
    scheduler_service.register_task(t)
    return {"code": 200, "message": "success", "data": {"id": t.id}}


@router.put("/{task_id}")
async def update_task(task_id: int, payload: dict, user=Depends(require_operator)):
    async with async_session() as session:
        t = await session.get(CustomTask, task_id)
        if not t:
            return {"code": 404, "message": "任务不存在", "data": None}
        editable = ("cluster_id", "name", "task_type", "target_resource", "target_namespace",
                    "cron_expression", "script_content", "parameters")
        for k in editable:
            if k in payload:
                setattr(t, k, payload[k])
        await session.commit()
    await scheduler_service.refresh_task(task_id)
    return {"code": 200, "message": "success", "data": None}


@router.delete("/{task_id}")
async def delete_task(task_id: int, user=Depends(require_admin)):
    scheduler_service.unregister_task(task_id)
    async with async_session() as session:
        t = await session.get(CustomTask, task_id)
        if t:
            await session.delete(t)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.post("/{task_id}/toggle")
async def toggle_task(task_id: int, user=Depends(require_operator)):
    async with async_session() as session:
        t = await session.get(CustomTask, task_id)
        if not t:
            return {"code": 404, "message": "任务不存在", "data": None}
        t.is_enabled = not t.is_enabled
        await session.commit()
    await scheduler_service.refresh_task(task_id)
    return {"code": 200, "message": "success", "data": {"is_enabled": t.is_enabled}}


@router.post("/{task_id}/run-now")
async def run_now(task_id: int, user=Depends(require_operator)):
    import asyncio
    asyncio.create_task(scheduler_service.execute_task(task_id))
    return {"code": 200, "message": "success", "data": {"status": "running"}}


@router.get("/{task_id}/logs")
async def task_logs(task_id: int, user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(select(CustomTaskLog).where(CustomTaskLog.task_id == task_id).order_by(CustomTaskLog.id.desc()).limit(50))
        logs = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": l.id, "status": l.status, "output": l.output, "error_message": l.error_message,
         "start_time": str(l.start_time), "end_time": str(l.end_time) if l.end_time else None}
        for l in logs
    ]}
