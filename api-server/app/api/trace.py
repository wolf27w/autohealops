# -*- coding: utf-8 -*-
"""Agent 运行轨迹 API（只读）：列表 / 详情 / 统计

复用现有表（alerts / ai_diagnosis_results / repair_plans / execution_logs / approvals /
repair_verifications / tickets / decision_results / diagnosis_failures），不新增表。
"""
from fastapi import APIRouter, Depends

from app.middleware.permission import require_any_user
from app.services import trace_service

router = APIRouter(prefix="/api/v1/agent-traces")


@router.get("/stats")
async def stats(user=Depends(require_any_user)):
    data = await trace_service.trace_stats(user=user)
    return {"code": 200, "message": "success", "data": data}


@router.get("")
async def list_traces(
    page: int = 1,
    page_size: int = 20,
    domain: str = "",
    result: str = "",
    has_approval: bool | None = None,
    has_ticket: bool | None = None,
    cluster_id: int | None = None,
    search: str = "",
    start: str = "",
    end: str = "",
    user=Depends(require_any_user),
):
    data = await trace_service.list_traces(
        page=page, page_size=page_size, domain=domain, result=result,
        has_approval=has_approval, has_ticket=has_ticket, cluster_id=cluster_id,
        search=search, start=start, end=end, user=user,
    )
    return {"code": 200, "message": "success", "data": data}


@router.get("/{trace_id}")
async def get_trace(trace_id: str, user=Depends(require_any_user)):
    data = await trace_service.get_trace(trace_id, user=user)
    if data is None:
        return {"code": 404, "message": "轨迹不存在或无权访问", "data": None}
    return {"code": 200, "message": "success", "data": data}
