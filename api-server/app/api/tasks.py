# -*- coding: utf-8 -*-
"""任务管理 API（状态机 + 审计时间线）"""
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_any_user, require_operator
from app.models import Alert, Task, TaskAuditLog

router = APIRouter(prefix="/api/v1/tasks")

# 状态机
TRANSITIONS = {
    "acknowledge": ("pending", "acknowledged"),
    "start": ("acknowledged", "in_progress"),
    "resolve": ("in_progress", "resolved"),
}


@router.get("")
async def list_tasks(status: str | None = None, user=Depends(require_any_user)):
    async with async_session() as session:
        query = select(Task)
        if status:
            query = query.where(Task.status == status)
        result = await session.execute(query.order_by(Task.id.desc()))
        tasks = list(result.scalars())
        data = []
        for t in tasks:
            alert = await session.get(Alert, t.alert_id)
            data.append({
                "id": t.id, "alert_id": t.alert_id, "assignee": t.assignee, "status": t.status,
                "alert_fault": alert.fault_type if alert else "", "created_at": str(t.created_at),
            })
    return {"code": 200, "message": "success", "data": data}


@router.get("/{task_id}")
async def get_task(task_id: int, user=Depends(require_any_user)):
    async with async_session() as session:
        t = await session.get(Task, task_id)
        result = await session.execute(select(TaskAuditLog).where(TaskAuditLog.task_id == task_id).order_by(TaskAuditLog.id.desc()))
        logs = list(result.scalars())
    if not t:
        return {"code": 404, "message": "任务不存在", "data": None}
    return {"code": 200, "message": "success", "data": {
        "task": {"id": t.id, "status": t.status, "resolution": t.resolution, "assignee": t.assignee},
        "timeline": [
            {"action": l.action, "old_status": l.old_status, "new_status": l.new_status,
             "operator": l.operator, "comment": l.comment, "created_at": str(l.created_at)}
            for l in logs
        ],
    }}


async def _transition(task_id: int, action: str, operator: str, comment: str = "", resolution: str = "") -> dict:
    """执行状态转换 + 写审计日志"""
    old_status, new_status = TRANSITIONS.get(action, ("", ""))
    if not old_status:
        return {"code": 400, "message": f"非法操作: {action}", "data": None}
    async with async_session() as session:
        t = await session.get(Task, task_id)
        if not t:
            return {"code": 404, "message": "任务不存在", "data": None}
        if t.status != old_status:
            return {"code": 400, "message": f"非法状态转换: {t.status} -> {new_status}", "data": None}
        t.status = new_status
        if action == "resolve":
            t.resolution = resolution
        session.add(TaskAuditLog(task_id=task_id, action=action, old_status=old_status,
                                 new_status=new_status, operator=operator, comment=comment))
        await session.commit()
    return {"code": 200, "message": "success", "data": {"status": new_status}}


@router.post("/{task_id}/acknowledge")
async def acknowledge(task_id: int, payload: dict = None, user=Depends(require_operator)):
    return await _transition(task_id, "acknowledge", user.username, (payload or {}).get("comment", ""))


@router.post("/{task_id}/start")
async def start(task_id: int, payload: dict = None, user=Depends(require_operator)):
    return await _transition(task_id, "start", user.username, (payload or {}).get("comment", ""))


@router.post("/{task_id}/resolve")
async def resolve(task_id: int, payload: dict = None, user=Depends(require_operator)):
    p = payload or {}
    return await _transition(task_id, "resolve", user.username, p.get("comment", ""), p.get("resolution", ""))
