# -*- coding: utf-8 -*-
"""审计追溯 API"""
from fastapi import APIRouter, Depends

from app.middleware.permission import require_admin, require_any_user
from app.services import audit_service

router = APIRouter(prefix="/api/v1/audit")


@router.get("/trace/{trace_id}")
async def trace(trace_id: str, user=Depends(require_any_user)):
    records = await audit_service.get_trace(trace_id)
    return {"code": 200, "message": "success", "data": {"records": records}}


@router.get("/search")
async def search(trace_id: str = "", page: int = 1, page_size: int = 20,
                cluster_id: int | None = None, namespace: str = "", resource: str = "",
                user=Depends(require_any_user)):
    result = await audit_service.search_audit(
        trace_id=trace_id, page=page, page_size=page_size,
        cluster_id=cluster_id, namespace=namespace, resource=resource,
    )
    return {"code": 200, "message": "success", "data": result}


@router.post("/verify-chain")
async def verify_chain(user=Depends(require_admin)):
    result = await audit_service.verify_audit_chain()
    return {"code": 200, "message": "success", "data": result}
