# -*- coding: utf-8 -*-
"""执行详情 API（前端 3 秒轮询修复计划执行进度 + 一键回滚）"""
import json
import time

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.clients.executor_client import executor_client
from app.core.database import async_session
from app.core.logging import get_logger
from app.middleware.permission import require_any_user, require_operator
from app.models import Alert, ExecutionLog, RepairPlan
from app.services import audit_service

router = APIRouter(prefix="/api/v1/executions")

logger = get_logger("api-server.executions")

# 可回滚的计划状态
_ROLLBACKABLE_STATUS = ("completed", "failed", "rolled_back")


def _rollback_steps(plan_json: str) -> list[str]:
    """提取 plan 中可执行的 rollback_command 列表"""
    try:
        steps = json.loads(plan_json or "{}").get("steps", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    return [s.get("rollback_command") for s in steps if isinstance(s, dict) and s.get("rollback_command")]


@router.get("/{plan_id}")
async def get_execution(plan_id: int, since_step: int = 0, user=Depends(require_any_user)):
    """返回修复计划执行进度（增量：step_index >= since_step）"""
    async with async_session() as session:
        result = await session.execute(
            select(ExecutionLog)
            .where(ExecutionLog.plan_id == plan_id, ExecutionLog.step_index >= since_step)
            .order_by(ExecutionLog.step_index)
        )
        logs = list(result.scalars())
        plan = await session.get(RepairPlan, plan_id)

    steps = [
        {"step_index": l.step_index, "command": l.command, "status": l.status, "output": l.output}
        for l in logs
    ]
    finished = (plan.status in ("completed", "failed", "rolled_back")) if plan else False
    return {"code": 200, "message": "success", "data": {
        "steps": steps,
        "logs": [l.output for l in logs if l.output],
        "finished": finished,
        "plan_status": plan.status if plan else "",
        "rollbackable": bool(plan and plan.status in _ROLLBACKABLE_STATUS and _rollback_steps(plan.plan_json)),
    }}


@router.post("/{plan_id}/rollback")
async def rollback_execution(plan_id: int, user=Depends(require_operator)):
    """一键回滚修复：按 plan 的 rollback_command 逆序执行（executor RollbackPlan）

    仅当计划已执行（completed/failed）且方案内含 rollback_command 时允许；
    回滚属变更操作，需 operator 及以上；全量写入审计。
    """
    async with async_session() as session:
        plan = await session.get(RepairPlan, plan_id)
        if not plan:
            return {"code": 404, "message": "修复计划不存在", "data": None}
        if plan.status not in _ROLLBACKABLE_STATUS:
            return {"code": 400, "message": f"当前状态不可回滚：{plan.status}", "data": None}
        rollback_cmds = _rollback_steps(plan.plan_json)
        if not rollback_cmds:
            return {"code": 400, "message": "该修复方案无可执行的回滚命令", "data": None}
        alert = await session.get(Alert, plan.alert_id)
        if not alert:
            return {"code": 404, "message": "关联告警不存在", "data": None}
        cluster_id = alert.cluster_id
        plan_json = plan.plan_json

    trace_id = f"rollback-{plan_id}-{int(time.time())}"
    try:
        result = await executor_client.acall(executor_client.rollback_plan, cluster_id, plan_id, plan_json, trace_id=trace_id)
    except Exception as e:  # noqa: BLE001
        logger.error("回滚执行失败 plan=%s: %s", plan_id, e)
        return {"code": 500, "message": f"回滚执行失败: {e}", "data": None}

    ok = bool(result.get("success"))
    # 已成功执行的步骤标记为已回滚 + 计划状态置 rolled_back（失败时不改状态，保留现场）
    if ok:
        async with async_session() as session:
            logs = (await session.execute(
                select(ExecutionLog).where(ExecutionLog.plan_id == plan_id)
            )).scalars().all()
            for l in logs:
                if l.status == "success":
                    l.status = "rolled_back"
            p = await session.get(RepairPlan, plan_id)
            if p:
                p.status = "rolled_back"
            await session.commit()

    try:
        await audit_service.create_audit_record(trace_id, decision_detail=json.dumps({
            "action": "rollback_repair", "plan_id": plan_id, "cluster_id": cluster_id,
            "operator": user.username, "success": ok,
            "executed": result.get("executed", []), "message": result.get("message", ""),
        }, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        logger.warning("回滚审计写入失败: %s", e)

    return {"code": 200 if ok else 500, "message": result.get("message", ""), "data": {
        "success": ok, "executed": result.get("executed", []),
    }}
