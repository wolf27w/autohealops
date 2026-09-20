# -*- coding: utf-8 -*-
"""批量处理 API"""
from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_any_user, require_operator
from app.models import BatchOperation
from app.services import batch_service

router = APIRouter(prefix="/api/v1/batch")


@router.post("")
async def create_batch(payload: dict, user=Depends(require_operator)):
    bid = await batch_service.create_batch_operation(
        payload.get("name", ""),
        payload.get("cluster_ids", []),
        payload.get("plan_id"),
        payload.get("strategy", "parallel"),
        payload.get("failure_policy", "continue"),
        payload.get("batch_size", 5),
    )
    return {"code": 200, "message": "success", "data": {"batch_operation_id": bid}}


@router.post("/{batch_id}/execute")
async def execute_batch(batch_id: int, user=Depends(require_operator)):
    import asyncio
    asyncio.create_task(batch_service.execute_batch(batch_id))
    return {"code": 200, "message": "success", "data": {"status": "running"}}


@router.get("")
async def list_batches(user=Depends(require_any_user)):
    async with async_session() as session:
        result = await session.execute(select(BatchOperation).order_by(BatchOperation.id.desc()))
        batches = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": b.id, "name": b.name, "strategy": b.strategy, "failure_policy": b.failure_policy,
         "status": b.status, "success_count": b.success_count, "failed_count": b.failed_count,
         "total_clusters": b.total_clusters, "created_at": str(b.created_at)}
        for b in batches
    ]}


@router.get("/{batch_id}")
async def get_batch(batch_id: int, user=Depends(require_any_user)):
    detail = await batch_service.get_batch_detail(batch_id)
    return {"code": 200, "message": "success", "data": detail}
